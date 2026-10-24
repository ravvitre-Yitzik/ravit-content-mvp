"""Local comparison orchestration. Only simulated providers; never reads keys."""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

import build_prompt
import grading
import run_log
from parse_response import ResponseError, parse_response

DEMO_REASON = "מצב הדגמה — לא בוצעה קריאת API"


class InputValidationError(ValueError):
    def __init__(self, completion_requests, problems):
        self.completion_requests = completion_requests
        self.problems = problems
        super().__init__("; ".join(completion_requests + problems))


def atomic_json_create(path, value):
    """Publish a complete JSON file atomically, refusing any existing target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
    return path


def _unavailable(model=None):
    return {"available": False, "model_id": model, "currency": None,
            "input_cost": None, "output_cost": None, "total": None, "reasons": [DEMO_REASON]}


def _demo_text(prop, variant):
    # Explicit allowlist: never interpolate address, building number or free-text impressions.
    facts = []
    for key, label in (("rooms", "חדרים"), ("area_sqm", "מ״ר")):
        field = build_prompt.get_field(prop, key)
        value = field.get("value")
        if field.get("status") == "known" and isinstance(value, (int, float)) and not isinstance(value, bool):
            facts.append(f"{value} {label}")
    detail = ", ".join(facts) or "פרטי הנכס שנמסרו"
    opening = "טיוטת הדגמה א׳" if variant == 0 else "טיוטת הדגמה ב׳"
    return json.dumps({"post": f"{opening}: {detail}. טקסט מדומה לתרגול ההשוואה בלבד.",
                       "hooks": [f"{opening} — {detail}. נוסח שיתוף לדוגמה {i}." for i in range(1, 6)]}, ensure_ascii=False)


class DemoComparisonService:
    def __init__(self, runs_dir=None, *, demo_callables=None):
        self.runs_dir = Path(runs_dir) if runs_dir is not None else build_prompt.ROOT / "runs" / "demo"
        self._calls = tuple(demo_callables) if demo_callables is not None else None
        if self._calls is not None and (len(self._calls) != 2 or not all(callable(c) for c in self._calls)):
            raise ValueError("נדרשים בדיוק שני ספקים מדומים")
        self._lock = RLock()

    def compare(self, property_data, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise ValueError("נדרש מזהה בקשה")
        with self._lock:
            # Snapshot before validation and generation, isolating callers from stored data.
            prop = json.loads(json.dumps(property_data, ensure_ascii=False))
            if not isinstance(prop, dict) or not isinstance(prop.get("fields", {}), dict):
                raise InputValidationError([], ["קלט הנכס חייב להכיל שדות במבנה תקין"])
            if any(not isinstance(f, dict) for f in prop.get("fields", {}).values()):
                raise InputValidationError([], ["שדה נכס חייב להיות אובייקט"])
            if any(not isinstance(f.get("status"), str) for f in prop.get("fields", {}).values()):
                raise InputValidationError([], ["מצב שדה חייב להיות מחרוזת תקינה"])
            template = build_prompt.load_json(build_prompt.TEMPLATE_PATH)
            requests, problems = build_prompt.check(template, prop)
            if requests or problems:
                raise InputValidationError(requests, problems)
            fingerprint = hashlib.sha256(json.dumps(prop, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for path in run_log.run_files(self.runs_dir):
                old = json.loads(path.read_text(encoding="utf-8"))
                if old.get("request_id") == request_id:
                    if old.get("input_sha256") != fingerprint:
                        raise ValueError("מזהה הבקשה כבר שימש לקלט אחר")
                    gpath = run_log.grading_path(path)
                    saved = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else None
                    run_log.write_log(self.runs_dir)
                    return {"run": old, "grading": saved, "already_generated": True}
            prompt = build_prompt.build_prompt(template, prop, build_prompt.load_instructions())
            run = {"run_id": str(uuid4()), "date": datetime.now(timezone.utc).isoformat(),
                   "mode": "demo", "request_id": request_id, "input_sha256": fingerprint,
                   "property": prop, "prompt": prompt, "results": [], "total_cost": _unavailable()}
            for i, provider in enumerate(("Google", "Anthropic")):
                model = f"demo-{provider.lower()}"
                result = {"provider": provider, "model": model, "settings": {"mode": "demo"},
                          "ok": False, "text": None, "stop_reason": "demo", "truncated": False,
                          "usage_raw": None, "usage": {"available": False, "input_tokens": None,
                          "output_tokens": None, "reasons": [DEMO_REASON]}, "error": None,
                          "rate": None, "cost": _unavailable(model), "parsed": None, "structure_errors": []}
                try:
                    response = self._calls[i](prompt) if self._calls else _demo_text(prop, i)
                    if isinstance(response, dict):
                        # Test doubles can use the existing provider result shape.
                        result.update({key: response[key] for key in ("ok", "text", "error", "truncated", "stop_reason") if key in response})
                    else:
                        result["text"], result["ok"] = response, True
                    if result["ok"]:
                        try:
                            result["parsed"] = parse_response(result["text"])
                        except (ResponseError, TypeError) as exc:
                            result["structure_errors"] = getattr(exc, "errors", ["תשובת הדגמה אינה טקסט תקין"])
                    if result["truncated"]:
                        result["structure_errors"].insert(0, "התשובה נקטעה במגבלת הפלט")
                except Exception as exc:
                    result["ok"], result["error"] = False, f"ספק ההדגמה נכשל: {type(exc).__name__}"
                run["results"].append(result)
            atomic_json_create(self.runs_dir / f"{run['run_id']}.json", run)
            run_log.write_log(self.runs_dir)
            return {"run": run, "grading": None, "already_generated": False}

    def grade(self, run_id, scores, choice, note=""):
        with self._lock:
            if not isinstance(note, str) or not isinstance(scores, dict):
                raise ValueError("ציונים והערה במבנה לא תקין")
            if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", run_id):
                raise ValueError("מזהה הרצה לא תקין")
            if not (self.runs_dir / f"{run_id}.json").is_file():
                raise FileNotFoundError("לא נמצאה הרצת הדגמה")
            path, run = grading.load_run(run_id, self.runs_dir)
            if run.get("mode") != "demo":
                raise ValueError("אפשר לדרג כאן הרצת הדגמה בלבד")
            errors = grading.check_grading(run, scores, choice)
            if errors:
                raise ValueError("; ".join(errors))
            gpath = run_log.grading_path(path)
            wanted_scores = {label: scores.get(label) for label in run_log.LABELS}
            if gpath.exists():
                saved = json.loads(gpath.read_text(encoding="utf-8"))
                if (saved["scores"], saved["choice"], saved["note"]) != (wanted_scores, choice, note or None):
                    raise ValueError("הדירוג כבר נשמר ואינו ניתן לשינוי")
                run_log.write_log(self.runs_dir)
                return {"run_id": run_id, "grading": saved, "already_saved": True}
            grading.save_grading(path, run, scores, choice, note)
            saved = json.loads(gpath.read_text(encoding="utf-8"))
            run_log.write_log(self.runs_dir)
            return {"run_id": run_id, "grading": saved, "already_saved": False}
