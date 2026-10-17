"""First generation run: the same fictional sample prompt to Google and Anthropic, one call each, no retries.

Without --run: prints the plan only and calls nothing.
With --run: calls both providers, checks each answer's structure, computes cost, saves runs/<run_id>.json
(including a snapshot of the rates used) and rebuilds runs/log.csv.
Run: python run_generation.py [--run]
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import providers
from build_prompt import ROOT
from check_common import read_key, sample_prompt
from cost import CostInputError, call_cost, comparison_total, format_amount
from parse_response import ResponseError, parse_response
from run_log import write_log

RATES_PATH = ROOT / "data" / "rates.json"
RUNS_DIR = ROOT / "runs"


def load_rates(path=RATES_PATH):
    return {r["model_id"]: r for r in json.loads(Path(path).read_text(encoding="utf-8"))["rates"]}


def evaluate(result, rates):
    """Add the structure check and cost to one provider result."""
    result["parsed"], result["structure_errors"] = None, []
    if result["ok"]:
        try:
            result["parsed"] = parse_response(result["text"])
        except ResponseError as e:
            result["structure_errors"] = e.errors
    if result["truncated"]:
        result["structure_errors"].insert(0, "התשובה נקטעה במגבלת הפלט")

    usage = result["usage"] or {"model_id": result["model"]}
    rate = rates.get(result["model"], {})
    result["rate"] = dict(rate) if rate else None  # snapshot: later rate changes must not change this record
    try:
        result["cost"] = call_cost(usage, rate)
    except CostInputError as e:
        result["cost"] = {"available": False, "model_id": result["model"], "currency": rate.get("currency"),
                          "input_cost": None, "output_cost": None, "total": None, "reasons": [str(e)]}
    return result


def _json_default(value):
    return str(value)  # Decimal amounts are saved as exact strings


def save_run(run, runs_dir=RUNS_DIR):
    runs_dir.mkdir(exist_ok=True)
    path = runs_dir / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return path


def print_plan(rates):
    print("תוכנית ההרצה (לא בוצעה קריאה):")
    print("- בקשה: הנכס הבדיוני בלבד, זהה לשני הספקים")
    print(f"- מגבלת פלט לכל ספק: {providers.MAX_OUTPUT_TOKENS} טוקנים, כולל טוקני חשיבה")
    print(f"- Google: {providers.GEMINI_MODEL}, רמת חשיבה {providers.GEMINI_THINKING_LEVEL}")
    print(f"- Anthropic: {providers.ANTHROPIC_MODEL}, חשיבה {providers.ANTHROPIC_THINKING['type']}")
    print("- ללא ניסיונות חוזרים")
    for model in (providers.GEMINI_MODEL, providers.ANTHROPIC_MODEL):
        r = rates[model]
        print(f"- תעריף {model}: קלט {r['input_price']} / פלט {r['output_price']} {r['currency']} למיליון טוקנים ({r['as_of']})")
    print("להרצה בפועל: python run_generation.py --run")


def print_result(r):
    print(f"\n=== {r['provider']} · {r['model']} ===")
    if r["error"]:
        print(f"נכשל: {r['error']}")
    elif r["structure_errors"]:
        print("התשובה נדחתה בבדיקת המבנה:")
        for err in r["structure_errors"]:
            print(f"- {err}")
    else:
        print(r["parsed"]["post"])
        print("\nמשפטי שיתוף:")
        for i, hook in enumerate(r["parsed"]["hooks"], 1):
            print(f"{i}. {hook}")
    u = r["usage"] or {}
    print(f"\nשימוש: קלט {u.get('input_tokens', 'לא זמין')}, פלט כולל חשיבה {u.get('output_tokens', 'לא זמין')}, עצירה: {r['stop_reason']}")
    c = r["cost"]
    print(f"עלות: {format_amount(c['total'], c['currency'])}")
    for reason in c["reasons"]:
        print(f"  סיבה: {reason}")


def main(argv, call_gemini=providers.call_gemini, call_anthropic=providers.call_anthropic):
    sys.stdout.reconfigure(encoding="utf-8")
    rates = load_rates()
    if "--run" not in argv[1:]:
        print_plan(rates)
        return 0

    prompt = sample_prompt()
    run = {"run_id": datetime.now().strftime("%Y%m%d-%H%M%S"), "date": datetime.now().isoformat(timespec="seconds"),
           "property": "data/sample-property.json (fictional)", "prompt": prompt, "results": []}
    for name, call in (("GEMINI_API_KEY", call_gemini), ("ANTHROPIC_API_KEY", call_anthropic)):
        key = read_key(name)
        if not key:
            print(f"נכשל: לא נמצא ערך ל־{name} בקובץ .env")
            return 1
        run["results"].append(evaluate(call(prompt, key), rates))

    run["total_cost"] = comparison_total([r["cost"] for r in run["results"]])
    path = save_run(run)
    write_log()
    for r in run["results"]:
        print_result(r)
    t = run["total_cost"]
    print(f"\nעלות ההשוואה: {format_amount(t['total'], t['currency'])}")
    for reason in t["reasons"]:
        print(f"  סיבה: {reason}")
    print(f"\nנשמר: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
