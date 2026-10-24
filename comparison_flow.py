"""Local comparison orchestration: default demo and explicitly enabled, single-use live experiment."""
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
    MODE = "demo"

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
            if run.get("mode") != self.MODE:
                raise ValueError("מצב ההרצה אינו תואם לשירות")
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


class LiveOutcomeUncertain(ValueError):
    """A durable reservation exists without a completed run: never issue another call."""


class LiveComparisonService(DemoComparisonService):
    """Explicit, single-use fictional-sample experiment. Construction never reads keys.

    The local budget is a conservative estimate from configured prices, UTF-8 bytes,
    schema overhead and the providers' full output limit; it is not a provider billing cap.
    A durable global slot is consumed BEFORE the first call, including uncertain failures.
    """
    MODE = "live"

    def __init__(self, runs_dir=None, *, enabled=False, max_cost_usd=None,
                 max_input_bytes=65536, calls=None, key_reader=None, rates=None, max_total_runs=1):
        from decimal import Decimal, InvalidOperation
        import providers
        from check_common import read_key
        if max_total_runs != 1 or isinstance(max_total_runs, bool):
            raise ValueError("אישור הבדיקה מוגבל להרצה חיה אחת בלבד")
        if not isinstance(max_input_bytes, int) or isinstance(max_input_bytes, bool) or max_input_bytes < 1:
            raise ValueError("מגבלת קלט לא תקינה")
        try:
            cap = Decimal(str(max_cost_usd)) if max_cost_usd is not None else None
        except InvalidOperation:
            raise ValueError("נדרשת תקרת עלות מפורשת בדולרים") from None
        if cap is not None and (not cap.is_finite() or cap <= 0 or cap > Decimal('0.50')):
            raise ValueError("תקרת הבדיקה חייבת להיות חיובית ולא לעלות על 0.50 דולר")
        super().__init__(Path(runs_dir) if runs_dir is not None else build_prompt.ROOT / 'runs' / 'ui-live')
        self.enabled = enabled is True
        self._cap = cap
        self._input_limit = max_input_bytes
        self._live_calls = tuple(calls) if calls is not None else (providers.call_gemini, providers.call_anthropic)
        if len(self._live_calls) != 2 or not all(callable(c) for c in self._live_calls):
            raise ValueError("נדרשים בדיוק שני ספקים")
        self._key_reader = key_reader or read_key
        self._rates = rates

    def _require_enabled(self):
        if not self.enabled or self._cap is None:
            raise ValueError("המצב החי דורש הפעלה ותקרת עלות מפורשות")

    def _sample_and_prompt(self, property_data):
        # Compare canonical JSON, not Python equality (True must not equal 1).
        sample = build_prompt.load_json(build_prompt.ROOT / 'data' / 'sample-property.json')
        template = build_prompt.load_json(build_prompt.TEMPLATE_PATH)
        if not isinstance(property_data, dict) or not isinstance(property_data.get('fields'), dict):
            raise InputValidationError([], ["במצב החי מותרת הדוגמה הבדיונית המקורית בלבד"])
        if set(property_data['fields']) - set(template['fields']):
            raise InputValidationError([], ["במצב החי מותרת הדוגמה הבדיונית המקורית בלבד"])
        def canonical(prop):
            fields = {}
            for key in template['fields']:
                field = build_prompt.get_field(prop, key)
                if not isinstance(field, dict):
                    raise ValueError()
                fields[key] = {'status': field.get('status'), 'value': field.get('value')}
            return json.dumps(fields, ensure_ascii=False, sort_keys=True, allow_nan=False)
        try:
            same = canonical(property_data) == canonical(sample)
        except (ValueError, TypeError):
            same = False
        if not same:
            raise InputValidationError([], ["במצב החי מותרת הדוגמה הבדיונית המקורית בלבד"])
        requests, problems = build_prompt.check(template, sample)
        if requests or problems:
            raise InputValidationError(requests, problems)
        prompt = build_prompt.build_prompt(template, sample, build_prompt.load_instructions())
        if len(prompt.encode('utf-8')) > self._input_limit:
            raise InputValidationError([], ["הבקשה חורגת ממגבלת הקלט המקומית"])
        return sample, prompt

    def _budget(self, prompt, rates):
        from decimal import Decimal
        from datetime import date
        from cost import call_cost, comparison_total
        import providers
        # UTF-8 bytes conservatively cover tokenization; extra allowance covers framing/schema.
        input_ceiling = len(prompt.encode('utf-8')) + len(json.dumps(providers.RESPONSE_SCHEMA, ensure_ascii=False).encode('utf-8')) + 4096
        estimates = []
        for model in (providers.GEMINI_MODEL, providers.ANTHROPIC_MODEL):
            rate = dict(rates.get(model) or {})
            try:
                if date.fromisoformat(rate["verified_on"]) > date.today():
                    raise ValueError()
                if model == providers.GEMINI_MODEL and date.fromisoformat(rate["paid_prices_valid_until"]) < date.today():
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise ValueError("תוקף אימות התעריפים חסר או פג; לא בוצעה קריאה לספקים") from None
            if model == providers.GEMINI_MODEL:
                if rate.get('paid_input_price') is None or rate.get('paid_output_price') is None:
                    raise ValueError("חסר תעריף Google בתשלום לחישוב אומדן שמרני")
                rate.update(input_price=rate['paid_input_price'], output_price=rate['paid_output_price'])
            estimate = call_cost({'model_id': model, 'input_tokens': input_ceiling,
                                  'output_tokens': providers.MAX_OUTPUT_TOKENS}, rate)
            if not estimate['available']:
                raise ValueError("אין תעריף תקין לחישוב אומדן הבדיקה")
            estimates.append(estimate)
        total = comparison_total(estimates)
        if not total['available'] or total['currency'] != 'USD' or total['total'] > self._cap:
            raise ValueError("האומדן השמרני חורג מתקרת העלות המאושרת")
        return {'currency': 'USD', 'estimated_max_total': str(total['total']),
                'approved_max_total': str(self._cap), 'input_token_ceiling_estimate': input_ceiling,
                'max_output_tokens_each': providers.MAX_OUTPUT_TOKENS,
                'method': 'UTF-8 bytes plus schema and 4096 framing allowance; full output limits; paid prices',
                'provider_estimates': json.loads(json.dumps(estimates, default=str)),
                'note': 'אומדן מקומי שמרני; אינו תקרת חיוב אצל הספק'}

    def get_by_request(self, request_id):
        """Recover a reserved request without keys or provider calls; never regenerate."""
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id):
            raise ValueError("מזהה בקשה לא תקין")
        with self._lock:
            slot = self.runs_dir / '.requests' / 'single-approved-experiment.json'
            if not slot.is_file():
                raise FileNotFoundError("לא נמצאה בקשה חיה שמורה")
            try:
                reservation = json.loads(slot.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                raise LiveOutcomeUncertain("מצב הבדיקה אינו ודאי; לא תבוצע קריאה נוספת") from None
            if not isinstance(reservation, dict):
                raise LiveOutcomeUncertain("מצב הבדיקה אינו ודאי; לא תבוצע קריאה נוספת")
            if reservation.get('request_id') != request_id:
                raise FileNotFoundError("לא נמצאה בקשה חיה שמורה")
            try:
                return self.get_run(reservation.get('run_id'))
            except (FileNotFoundError, ValueError):
                raise LiveOutcomeUncertain("תוצאת הבדיקה אינה ודאית; לא תבוצע קריאה נוספת") from None

    def get_run(self, run_id):
        with self._lock:
            if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', run_id):
                raise ValueError("מזהה הרצה לא תקין")
            path = self.runs_dir / f'{run_id}.json'
            if not path.is_file():
                raise FileNotFoundError("לא נמצאה הרצה חיה שמורה")
            run = json.loads(path.read_text(encoding='utf-8'))
            if run.get('mode') != 'live':
                raise ValueError("מצב ההרצה אינו תואם לשירות")
            gpath = run_log.grading_path(path)
            saved = json.loads(gpath.read_text(encoding='utf-8')) if gpath.exists() else None
            run_log.write_log(self.runs_dir)
            return {'run': run, 'grading': saved}

    def compare(self, property_data, request_id):
        self._require_enabled()
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id):
            raise ValueError("נדרש מזהה בקשה")
        with self._lock:
            from run_generation import evaluate, load_rates
            from cost import comparison_total
            import providers
            sample, prompt = self._sample_and_prompt(property_data)
            fingerprint = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
            slot = self.runs_dir / '.requests' / 'single-approved-experiment.json'
            if slot.exists():
                try:
                    reservation = json.loads(slot.read_text(encoding='utf-8'))
                except (ValueError, OSError):
                    raise LiveOutcomeUncertain("מצב הבדיקה אינו ודאי; לא תבוצע קריאה נוספת") from None
                if reservation.get('request_id') != request_id:
                    raise ValueError("ההרשאה לבדיקה החיה היחידה כבר נוצלה; לא תבוצע קריאה נוספת")
                if reservation.get('input_sha256') != fingerprint:
                    raise ValueError("מזהה הבקשה כבר שימש לבקשה אחרת")
                try:
                    restored = self.get_run(reservation.get('run_id'))
                except FileNotFoundError:
                    raise LiveOutcomeUncertain("תוצאת הבדיקה אינה ודאית; לא תבוצע קריאה נוספת") from None
                return {**restored, 'already_generated': True}
            # A manually moved/legacy result still consumes the one-run authorization.
            if run_log.run_files(self.runs_dir):
                raise ValueError("כבר קיימת הרצה חיה; נדרש אישור חדש לפני קריאה נוספת")
            rates = self._rates if self._rates is not None else load_rates()
            budget = self._budget(prompt, rates)
            try:
                keys = [self._key_reader(name) for name in ('GEMINI_API_KEY', 'ANTHROPIC_API_KEY')]
            except Exception:
                raise ValueError("לא ניתן לקרוא את שני המפתחות; לא בוצעה קריאה לספקים") from None
            if any(not isinstance(key, str) or not key.strip() for key in keys):
                raise ValueError("חסרים מפתחות לשני הספקים; לא בוצעה קריאה לספקים")
            run_id = str(uuid4())
            reservation = {'run_id': run_id, 'request_id': request_id, 'input_sha256': fingerprint,
                           'reserved_at': datetime.now(timezone.utc).isoformat(), 'mode': 'live',
                           'budget_estimate': budget}
            try:
                atomic_json_create(slot, reservation)
            except FileExistsError:
                # Another process acquired the global allowance. Never call either provider.
                raise LiveOutcomeUncertain("הבדיקה כבר התחילה; לא תבוצע קריאה נוספת") from None
            run = {'run_id': run_id, 'date': reservation['reserved_at'], 'mode': 'live',
                   'request_id': request_id, 'input_sha256': fingerprint,
                   'property': sample, 'prompt': prompt, 'results': [], 'budget_estimate': budget}
            for index, (provider, model) in enumerate((('Google', providers.GEMINI_MODEL), ('Anthropic', providers.ANTHROPIC_MODEL))):
                try:
                    result = self._live_calls[index](prompt, keys[index])
                    # Redact both credentials from all serializable response material before storage.
                    encoded = json.dumps(result, ensure_ascii=False, default=str)
                    for key in keys:
                        encoded = encoded.replace(key, '***')
                    result = json.loads(encoded)
                    if not isinstance(result, dict) or result.get('model') != model or result.get('provider') != provider:
                        raise ValueError('unexpected provider result')
                except Exception as exc:
                    result = {'provider': provider, 'model': model, 'settings': {}, 'ok': False,
                              'text': None, 'stop_reason': None, 'truncated': False,
                              'usage': None, 'usage_raw': None, 'error': f'קריאת הספק נכשלה ({type(exc).__name__}); לא בוצע ניסיון חוזר'}
                result = evaluate(result, rates)
                if provider == 'Google' and rates[model].get('billing_tier') not in ('free', 'paid'):
                    paid_rates = {**rates, model: {**rates[model], 'input_price': rates[model]['paid_input_price'],
                                                  'output_price': rates[model]['paid_output_price']}}
                    result['paid_cost_estimate'] = evaluate(dict(result), paid_rates)['cost']
                    result['rate']['billing_tier'] = 'unknown'
                    result['cost'] = {'available': False, 'model_id': model, 'currency': 'USD',
                                      'input_cost': None, 'output_cost': None, 'total': None,
                                      'reasons': ['מסלול החיוב של Google לא אומת; האומדן בתשלום מוצג בנפרד']}
                elif provider == 'Google' and rates[model].get('billing_tier') == 'paid':
                    paid_rates = {**rates, model: {**rates[model], 'input_price': rates[model]['paid_input_price'],
                                                  'output_price': rates[model]['paid_output_price']}}
                    result = evaluate(result, paid_rates)
                result = json.loads(json.dumps(result, ensure_ascii=False, default=str))
                atomic_json_create(slot.parent / f'{run_id}-provider-{index}.json', result)
                run['results'].append(result)
            run['total_cost'] = comparison_total([r['cost'] for r in run['results']])
            run = json.loads(json.dumps(run, ensure_ascii=False, default=str))
            atomic_json_create(self.runs_dir / f'{run_id}.json', run)
            run_log.write_log(self.runs_dir)
            return {'run': run, 'grading': None, 'already_generated': False}


class OperationalComparisonService(LiveComparisonService):
    """Editable local workflow: explicit consent and a separate durable intent per request."""
    MODE = 'work'
    MAX_PROPERTY_BYTES = 256 * 1024
    MAX_FIELD_CHARS = 10000
    NUMERIC_FIELDS = {'floor', 'area_sqm', 'rooms', 'price', 'total_floors', 'balcony_area_sqm', 'garden_area_sqm'}

    def __init__(self, runs_dir=None, *, enabled=False, max_cost_usd=None,
                 max_input_bytes=65536, calls=None, key_reader=None, rates=None):
        super().__init__(Path(runs_dir) if runs_dir is not None else build_prompt.ROOT / 'runs' / 'work',
                         enabled=enabled, max_cost_usd=max_cost_usd, max_input_bytes=max_input_bytes,
                         calls=calls, key_reader=key_reader, rates=rates)

    @staticmethod
    def _request_id(request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id):
            raise ValueError('מזהה בקשה לא תקין')
        return request_id

    def _intent_path(self, request_id):
        self._request_id(request_id)
        digest = hashlib.sha256(request_id.encode('ascii')).hexdigest()
        return self.runs_dir / '.requests' / f'{digest}.json'

    def _validated_property(self, property_data):
        import math
        try:
            encoded = json.dumps(property_data, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode('utf-8')) > self.MAX_PROPERTY_BYTES:
                raise ValueError()
            prop = json.loads(encoded)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise InputValidationError([], ['קלט הנכס אינו תקין או גדול מדי']) from None
        if not isinstance(prop, dict) or set(prop) - {'fields', 'example_id', 'data_note', 'sources'} or not isinstance(prop.get('fields'), dict):
            raise InputValidationError([], ['נדרש אובייקט נתוני נכס עם שדות מוכרים בלבד'])
        template = build_prompt.load_json(build_prompt.TEMPLATE_PATH)
        if set(prop['fields']) - set(template['fields']):
            raise InputValidationError([], ['נמצאו שדות שאינם בתבנית'])
        normalized, problems = {'fields': {}}, []
        for key, spec in template['fields'].items():
            field = build_prompt.get_field(prop, key)
            label = spec['label']
            if not isinstance(field, dict) or set(field) - {'status', 'value', 'source'}:
                problems.append(f'{label}: מבנה שדה לא תקין')
                continue
            status, value = field.get('status'), field.get('value')
            if not isinstance(status, str) or status not in build_prompt.STATUSES:
                problems.append(f'{label}: מצב מידע לא תקין')
                continue
            if status != 'known':
                value = None  # Inactive form contents must never enter the prompt or saved property.
            elif 'allowed_values' in spec:
                if not any(type(value) is type(allowed) and value == allowed for allowed in spec['allowed_values']):
                    problems.append(f'{label}: ערך לא מותר')
            elif key in self.NUMERIC_FIELDS:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
                    problems.append(f'{label}: נדרש מספר סופי')
                elif key in {'price', 'rooms', 'area_sqm'} and value <= 0:
                    problems.append(f'{label}: נדרש מספר חיובי')
                elif key in {'total_floors', 'balcony_area_sqm', 'garden_area_sqm'} and value < 0:
                    problems.append(f'{label}: נדרש מספר שאינו שלילי')
                elif key in {'floor', 'total_floors'} and value != int(value):
                    problems.append(f'{label}: נדרש מספר שלם')
            elif spec.get('multiple'):
                if (not isinstance(value, list) or len(value) > 64
                        or any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in value)):
                    problems.append(f'{label}: נדרשת רשימה מוגבלת של ערכי טקסט')
            elif not isinstance(value, str) or len(value) > self.MAX_FIELD_CHARS:
                problems.append(f'{label}: נדרש טקסט באורך מותר')
            normalized['fields'][key] = {'status': status, 'value': value}
        if problems:
            raise InputValidationError([], problems)
        requests, problems = build_prompt.check(template, normalized)
        if requests or problems:
            raise InputValidationError(requests, problems)
        return normalized, template

    def preview(self, property_data):
        """Server-validated review before consent; no keys, reservation or provider calls."""
        self._require_enabled()
        from run_generation import load_rates
        prop, template = self._validated_property(property_data)
        prompt = build_prompt.build_prompt(template, prop, build_prompt.load_instructions())
        if len(prompt.encode('utf-8')) > self._input_limit:
            raise InputValidationError([], ['הבקשה חורגת ממגבלת הקלט המקומית'])
        rates = json.loads(json.dumps(self._rates if self._rates is not None else load_rates(), default=str))
        return {'property': prop, 'budget_estimate': self._budget(prompt, rates)}

    def _write_log(self):
        return run_log.write_log(self.runs_dir)

    def _read_intent(self, request_id):
        path = self._intent_path(request_id)
        if not path.is_file():
            raise FileNotFoundError('לא נמצאה בקשת עבודה שמורה')
        try:
            intent = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(intent, dict) or intent.get('request_id') != request_id or intent.get('mode') != self.MODE:
                raise ValueError()
        except (ValueError, OSError):
            raise LiveOutcomeUncertain('מצב הבקשה אינו ודאי; לא תבוצע קריאה נוספת') from None
        return intent

    def get_by_request(self, request_id):
        with self._lock:
            intent = self._read_intent(request_id)
            try:
                return self.get_run(intent.get('run_id'))
            except (ValueError, FileNotFoundError):
                raise LiveOutcomeUncertain('תוצאת הבקשה אינה ודאית; לא תבוצע קריאה נוספת') from None

    def get_run(self, run_id):
        with self._lock:
            if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', run_id):
                raise ValueError('מזהה הרצה לא תקין')
            path = self.runs_dir / f'{run_id}.json'
            if not path.is_file():
                raise FileNotFoundError('לא נמצאה הרצת עבודה שמורה')
            run = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(run, dict) or run.get('mode') != self.MODE or run.get('run_id') != run_id:
                raise ValueError('מצב ההרצה אינו תואם לשירות')
            gpath = run_log.grading_path(path)
            saved = json.loads(gpath.read_text(encoding='utf-8')) if gpath.exists() else None
            return {'run': run, 'grading': saved}

    def list_runs(self):
        with self._lock:
            rows, completed = [], set()
            for path in run_log.run_files(self.runs_dir):
                try:
                    record = self.get_run(path.stem)
                    run, saved = record['run'], record['grading']
                    valid = sum(run_log.package_status(result) == 'תקין' for result in run['results'])
                    rows.append({'run_id': run['run_id'], 'request_id': run['request_id'], 'date': run['date'],
                                 'status': 'completed' if valid == 2 else 'partial_failure' if valid else 'failed',
                                 'graded': saved is not None, 'choice': saved.get('choice') if saved else None,
                                 'total_cost': {k: (run.get('total_cost') or {}).get(k) for k in ('available', 'currency', 'total')}})
                    completed.add(run['run_id'])
                except (ValueError, KeyError, TypeError, OSError):
                    continue  # A corrupt record must not hide other history entries.
            for path in (self.runs_dir / '.requests').glob('*.json'):
                if not re.fullmatch(r'[a-f0-9]{64}', path.stem):
                    continue  # Provider checkpoints are private; not history records.
                try:
                    intent = json.loads(path.read_text(encoding='utf-8'))
                    if intent.get('mode') != self.MODE or intent.get('run_id') in completed:
                        continue
                    self._request_id(intent.get('request_id'))
                    if self._intent_path(intent['request_id']).name != path.name:
                        continue
                    rows.append({'run_id': intent.get('run_id'), 'request_id': intent['request_id'],
                                 'date': intent.get('reserved_at'), 'status': 'uncertain',
                                 'graded': False, 'choice': None, 'total_cost': None})
                except (ValueError, AttributeError, KeyError, TypeError, OSError):
                    continue
            return {'runs': sorted(rows, key=lambda row: str(row.get('date') or ''), reverse=True)}

    def _provider_result(self, index, prompt, keys, rates):
        import providers
        from run_generation import evaluate
        provider, model = (('Google', providers.GEMINI_MODEL), ('Anthropic', providers.ANTHROPIC_MODEL))[index]
        def redact(value):
            if isinstance(value, str):
                for key in keys:
                    value = value.replace(key, '***')
                return value
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return {redact(key): redact(item) for key, item in value.items()}
            return value
        def failed():
            return {'provider': provider, 'model': model, 'settings': {}, 'ok': False, 'text': None,
                    'stop_reason': None, 'truncated': False, 'usage': None, 'usage_raw': None,
                    'error': 'קריאת הספק נכשלה; לא בוצע ניסיון חוזר'}
        try:
            result = json.loads(json.dumps(self._live_calls[index](prompt, keys[index]), default=str, allow_nan=False))
            result = redact(result)
            if not isinstance(result, dict) or result.get('provider') != provider or result.get('model') != model:
                raise ValueError()
            result = evaluate(result, rates)
        except Exception:
            result = evaluate(failed(), rates)
        if provider == 'Google':
            tier = rates[model].get('billing_tier')
            paid_rates = {**rates, model: {**rates[model], 'input_price': rates[model]['paid_input_price'],
                                          'output_price': rates[model]['paid_output_price']}}
            if tier == 'paid':
                result = evaluate(result, paid_rates)
            elif tier != 'free':
                result['paid_cost_estimate'] = evaluate(dict(result), paid_rates)['cost']
                result['rate']['billing_tier'] = 'unknown'
                result['rate']['input_price'] = None
                result['rate']['output_price'] = None
                result['cost'] = {'available': False, 'model_id': model, 'currency': 'USD',
                                  'input_cost': None, 'output_cost': None, 'total': None,
                                  'reasons': ['מסלול החיוב של Google לא אומת; האומדן בתשלום מוצג בנפרד']}
        return json.loads(json.dumps(result, ensure_ascii=False, default=str, allow_nan=False))

    def compare(self, property_data, request_id, *, data_consent=False):
        self._require_enabled()
        self._request_id(request_id)
        if data_consent is not True:
            raise ValueError('נדרשת הסכמה מפורשת לשליחת כל נתוני הנכס לשני הספקים')
        with self._lock:
            from run_generation import load_rates
            from cost import comparison_total
            prop, template = self._validated_property(property_data)
            fingerprint = hashlib.sha256(json.dumps(prop, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            slot = self._intent_path(request_id)
            if slot.exists():
                intent = self._read_intent(request_id)
                if intent.get('input_sha256') != fingerprint:
                    raise ValueError('מזהה הבקשה כבר שימש לקלט אחר')
                restored = self.get_by_request(request_id)
                self._write_log()
                return {**restored, 'already_generated': True}
            prompt = build_prompt.build_prompt(template, prop, build_prompt.load_instructions())
            if len(prompt.encode('utf-8')) > self._input_limit:
                raise InputValidationError([], ['הבקשה חורגת ממגבלת הקלט המקומית'])
            rates = json.loads(json.dumps(self._rates if self._rates is not None else load_rates(), default=str))
            budget = self._budget(prompt, rates)
            try:
                keys = [self._key_reader(name) for name in ('GEMINI_API_KEY', 'ANTHROPIC_API_KEY')]
            except Exception:
                raise ValueError('לא ניתן לקרוא את שני המפתחות; לא בוצעה קריאה לספקים') from None
            if any(not isinstance(key, str) or not key.strip() for key in keys):
                raise ValueError('חסרים מפתחות לשני הספקים; לא בוצעה קריאה לספקים')
            run_id = str(uuid4())
            intent = {'run_id': run_id, 'request_id': request_id, 'input_sha256': fingerprint,
                      'reserved_at': datetime.now(timezone.utc).isoformat(), 'mode': self.MODE,
                      'budget_estimate': budget, 'data_consent': True}
            try:
                atomic_json_create(slot, intent)
            except FileExistsError:
                raise LiveOutcomeUncertain('הבקשה כבר התחילה; לא תבוצע קריאה נוספת') from None
            run = {'run_id': run_id, 'request_id': request_id, 'date': intent['reserved_at'], 'mode': self.MODE,
                   'property': prop, 'input_sha256': fingerprint, 'prompt': prompt, 'results': [],
                   'budget_estimate': budget, 'data_consent': True}
            for index in range(2):
                result = self._provider_result(index, prompt, keys, rates)
                atomic_json_create(slot.parent / f'{run_id}-provider-{index}.json', result)
                run['results'].append(result)
            run['total_cost'] = comparison_total([result['cost'] for result in run['results']])
            run = json.loads(json.dumps(run, ensure_ascii=False, default=str, allow_nan=False))
            atomic_json_create(self.runs_dir / f'{run_id}.json', run)
            self._write_log()
            return {'run': run, 'grading': None, 'already_generated': False}

    def grade(self, run_id, scores, choice, note=''):
        with self._lock:
            if (not isinstance(scores, dict) or set(scores) - set(run_log.LABELS)
                    or not isinstance(choice, str) or not isinstance(note, str) or len(note) > self.MAX_FIELD_CHARS):
                raise ValueError('נתוני דירוג לא תקינים')
            record = self.get_run(run_id)
            run, saved = record['run'], record['grading']
            errors = grading.check_grading(run, scores, choice)
            if errors:
                raise ValueError('; '.join(errors))
            wanted = {label: scores.get(label) for label in run_log.LABELS}
            already_saved = saved is not None
            if saved is None:
                path = self.runs_dir / f'{run_id}.json'
                try:
                    grading.save_grading(path, run, scores, choice, note)
                except FileExistsError:
                    already_saved = True  # Another process atomically published a grading.
                saved = self.get_run(run_id)['grading']
            if saved is None or (saved['scores'], saved['choice'], saved['note']) != (wanted, choice, note or None):
                raise ValueError('הדירוג כבר נשמר ואינו ניתן לשינוי')
            self._write_log()
            return {'run_id': run_id, 'grading': saved, 'already_saved': already_saved}
