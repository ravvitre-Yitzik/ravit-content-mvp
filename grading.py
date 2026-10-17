"""Grade a saved run: a 1–5 score per package and a choice of א, ב or none. The choice is not approval to publish.

The grading is saved in runs/<run_id>.grading.json next to the run; the run file itself is never changed.
A run that already has a grading is shown and left as is, so no duplicate records are created.
Run: python grading.py <run_id>
"""
import json
import re
import sys
from datetime import datetime

import run_log
from run_generation import print_result
from run_log import LABELS, grading_path, package_status

NONE_CHOICE = "אף אחת"
SCALE = {5: "מוכן לפרסום ללא שינוי", 4: "מתאים לאחר תיקונים נקודתיים קלים", 3: "הכיוון מתאים, אך דרוש ליטוש משמעותי",
         2: "נדרשת כתיבה מחדש של חלקים מרכזיים", 1: "אינו שימושי או כולל שגיאות מהותיות"}


def load_run(run_id, runs_dir=None):
    if not re.fullmatch(r"[\w-]+", run_id):
        raise ValueError(f"מזהה הרצה לא תקין: {run_id}")
    path = (runs_dir or run_log.RUNS_DIR) / f"{run_id}.json"
    if not path.exists():
        raise ValueError(f"לא נמצאה הרצה: {run_id}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def packages(run):
    """[(label, result)] with א = first result in the run file and ב = second."""
    if len(run["results"]) != len(LABELS):
        raise ValueError("בהרצה צריכות להיות בדיוק שתי חבילות")
    return list(zip(LABELS, run["results"]))


def check_grading(run, scores, choice):
    """Problems in Hebrew; empty means the grading can be saved."""
    errors = []
    valid = [label for label, r in packages(run) if package_status(r) == "תקין"]
    for label, _ in packages(run):
        score = scores.get(label)
        if label in valid:
            if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
                errors.append(f"חבילה {label}׳: נדרש ציון שלם בין 1 ל־5")
        elif score is not None:
            errors.append(f"חבילה {label}׳ נכשלה או נדחתה ולכן לא מקבלת ציון")
    if choice != NONE_CHOICE and choice not in valid:
        errors.append(f"בחירה לא תקפה: {choice}. אפשר לבחור {', '.join(valid + [NONE_CHOICE])}")
    return errors


def save_grading(run_path, run, scores, choice, note=None):
    gpath = grading_path(run_path)
    if gpath.exists():
        raise FileExistsError(f"להרצה כבר יש דירוג: {gpath.name}")
    errors = check_grading(run, scores, choice)
    if errors:
        raise ValueError("; ".join(errors))
    grading = {
        "run_id": run["run_id"],
        "graded_at": datetime.now().isoformat(timespec="seconds"),
        "mapping": {label: {"provider": r["provider"], "model": r["model"]} for label, r in packages(run)},
        "scores": {label: scores.get(label) for label in LABELS},
        "choice": choice,
        "note": note or None,
        "publication_approval": False,
        "scale_source": "approved-spec.pdf, עמוד 2",
    }
    gpath.write_text(json.dumps(grading, ensure_ascii=False, indent=2), encoding="utf-8")
    return gpath


def _ask_score(label, ask):
    while True:
        answer = ask(f"ציון לחבילה {label}׳ (1–5): ").strip()
        if answer in {"1", "2", "3", "4", "5"}:
            return int(answer)
        print("נדרש מספר שלם בין 1 ל־5.")


def _ask_choice(valid, ask):
    options = valid + [NONE_CHOICE]
    while True:
        answer = ask(f"בחירה ({' / '.join(options)}): ").strip().rstrip("׳'")
        if answer in options:
            return answer
        print(f"יש לבחור אחת מהאפשרויות: {', '.join(options)}")


def main(argv, ask=input):
    sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 2:
        print("שימוש: python grading.py <מזהה הרצה>")
        return 2
    try:
        run_path, run = load_run(argv[1])
        pkgs = packages(run)
    except ValueError as e:
        print(e)
        return 1

    gpath = grading_path(run_path)
    if gpath.exists():
        g = json.loads(gpath.read_text(encoding="utf-8"))
        print(f"להרצה {run['run_id']} כבר יש דירוג; לא נוצרה רשומה נוספת.")
        print(f"ציונים: {g.get('scores')} · בחירה: {g.get('choice')} · הערה: {g.get('note') or '—'}")
        return 0

    print(f"הרצה {run['run_id']} ({run.get('date')})")
    for label, r in pkgs:
        print(f"{label}׳ = {r['provider']} · {r['model']}")
    for label, r in pkgs:
        print(f"\n######## חבילה {label}׳ ########")
        print_result(r)

    print("\nסולם הדירוג (האפיון המאושר):")
    for score, meaning in SCALE.items():
        print(f"{score} — {meaning}")
    print("טעות עובדתית מהותית או חשיפת מידע פנימי מונעות ציון 4 או 5.\n")

    valid = [label for label, r in pkgs if package_status(r) == "תקין"]
    scores = {}
    for label, r in pkgs:
        if label in valid:
            scores[label] = _ask_score(label, ask)
        else:
            print(f"חבילה {label}׳: {package_status(r)} — ללא ציון.")
    choice = _ask_choice(valid, ask)
    note = ask("הערה קצרה (אפשר להשאיר ריק): ").strip()

    path = save_grading(run_path, run, scores, choice, note)
    run_log.write_log()
    print(f"\nנשמר: runs/{path.name}. הבחירה אינה אישור לפרסום.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
