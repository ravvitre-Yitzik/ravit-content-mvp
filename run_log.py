"""Summary log (CSV) of all saved runs: one row per provider package, linked to the full run file.

The log is rebuilt from runs/*.json and runs/*.grading.json every time, so it never holds duplicate rows.
Missing data is written as "לא זמין", never as 0.
Run: python run_log.py
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

from build_prompt import ROOT

RUNS_DIR = ROOT / "runs"
LOG_NAME = "log.csv"
UNAVAILABLE = "לא זמין"
LABELS = ["א", "ב"]  # by the order of results in the run file
COLUMNS = ["run_id", "date", "label", "provider", "model", "status", "structure_errors",
           "input_tokens", "output_tokens", "input_price", "output_price", "unit", "currency", "rate_date",
           "call_cost", "comparison_cost", "score", "choice", "note", "prompt_sha256", "full_output"]


def _value(v):
    return UNAVAILABLE if v is None or v == "" else v


def package_status(result):
    if not result.get("ok"):
        return "נכשל"
    if result.get("structure_errors"):
        return "נדחה בבדיקת המבנה"
    return "תקין"


def run_files(runs_dir):
    return sorted(p for p in Path(runs_dir).glob("*.json") if not p.name.endswith(".grading.json"))


def grading_path(run_path):
    return run_path.with_name(run_path.stem + ".grading.json")


def rows_for_run(run_path):
    run = json.loads(run_path.read_text(encoding="utf-8"))
    gpath = grading_path(run_path)
    grading = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else {}
    scores = grading.get("scores", {})
    total = run.get("total_cost") or {}
    rows = []
    for label, r in zip(LABELS, run["results"]):
        rate = r.get("rate") or {}          # runs saved before the snapshot existed have none
        usage = r.get("usage") or {}
        cost = r.get("cost") or {}
        rows.append({
            "run_id": run["run_id"],
            "date": run.get("date"),
            "label": label,
            "provider": r.get("provider"),
            "model": r.get("model"),
            "status": package_status(r),
            "structure_errors": "; ".join(r.get("structure_errors") or []) or (r.get("error") or ""),
            "input_tokens": _value(usage.get("input_tokens")),
            "output_tokens": _value(usage.get("output_tokens")),
            "input_price": _value(rate.get("input_price")),
            "output_price": _value(rate.get("output_price")),
            "unit": _value(rate.get("unit")),
            "currency": _value(rate.get("currency") or cost.get("currency")),
            "rate_date": _value(rate.get("as_of") or ("תעריף לבדיקה" if rate.get("test_rate") else None)),
            "call_cost": _value(cost.get("total") if cost.get("available") else None),
            "comparison_cost": _value(total.get("total") if total.get("available") else None),
            "score": _value(scores.get(label)),
            "choice": _value(grading.get("choice")),
            "note": grading.get("note") or "",
            "prompt_sha256": hashlib.sha256(run.get("prompt", "").encode("utf-8")).hexdigest()[:16],
            "full_output": f"runs/{run_path.name}",
        })
    return rows


def write_log(runs_dir=None):
    runs_dir = runs_dir or RUNS_DIR
    path = Path(runs_dir) / LOG_NAME
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows Hebrew correctly
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for run_path in run_files(runs_dir):
            writer.writerows(rows_for_run(run_path))
    return path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    path = write_log()
    print(f"הלוג נבנה מחדש: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
