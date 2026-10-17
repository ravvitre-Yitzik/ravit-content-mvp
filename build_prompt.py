"""Load the template, a property file and the writing instructions; check the input; build one shared prompt.

No network calls. Run: python build_prompt.py data/sample-property.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TEMPLATE_PATH = ROOT / "data" / "property-template.json"
INSTRUCTIONS_PATH = ROOT / "instructions" / "course-writing-instructions.md"

STATUSES = {"known", "not_provided", "confirmed_unknown", "conflicting"}
UNKNOWN_TEXT = "לא ידוע"  # shown for both not_provided and confirmed_unknown; never turns into "no"
INTERNAL_MARK = "[פנימי, לא לפרסום]"  # same wording as the privacy section in the instructions


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_instructions(path=INSTRUCTIONS_PATH):
    """Instructions text without the leading source comment (meant for humans, not providers)."""
    text = Path(path).read_text(encoding="utf-8")
    return re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.DOTALL).strip()


def is_empty(value):
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def get_field(prop, key):
    return prop.get("fields", {}).get(key, {"status": "not_provided", "value": None})


def is_required(spec, prop):
    if spec["required"]:
        return True
    condition = spec.get("required_if")
    if not condition:
        return False
    return all(
        get_field(prop, key)["status"] == "known" and get_field(prop, key).get("value") == expected
        for key, expected in condition.items()
    )


def check(template, prop):
    """Return (completion_requests, problems). A draft may be generated only when both are empty.

    completion_requests: required fields that were not provided (one consolidated request).
    problems: conflicts and invalid input that must be fixed.
    """
    requests, problems = [], []
    tfields = template["fields"]

    for key in prop.get("fields", {}):
        if key not in tfields:
            problems.append(f"שדה לא מוכר בתבנית: {key}")

    for key, spec in tfields.items():
        label = spec["label"]
        field = get_field(prop, key)
        status, value = field.get("status"), field.get("value")

        if status not in STATUSES:
            problems.append(f"{label}: מצב לא חוקי '{status}'")
        elif status == "conflicting":
            problems.append(f"{label}: נתונים סותרים, יש לתקן לפני הפקה")
        elif status == "not_provided":
            if is_required(spec, prop):
                requests.append(label)
        elif status == "confirmed_unknown":
            if spec.get("must_be_known"):
                problems.append(f"{label}: חייב להיות ידוע כדי להפיק טיוטה")
        elif is_empty(value):
            problems.append(f"{label}: מסומן ידוע אך אין ערך")
        elif spec.get("multiple"):
            if not isinstance(value, list):
                problems.append(f"{label}: נדרשת רשימת ערכים")
        elif "allowed_values" in spec and value not in spec["allowed_values"]:
            problems.append(f"{label}: ערך לא מותר '{value}'")

    return requests, problems


def format_value(key, field, prop):
    if field["status"] != "known":
        return UNKNOWN_TEXT
    value = field["value"]
    if value is True:
        return "יש"
    if value is False:
        return "לא קיים"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if key == "price":
        deal = get_field(prop, "deal_type")["value"]
        return f"{value} ₪ לחודש" if deal == "השכרה" else f"{value} ₪ מחיר כולל"
    return str(value)


def build_prompt(template, prop, instructions):
    """One prompt, identical for every provider. Call check() first."""
    lines = []
    for key, spec in template["fields"].items():
        line = f"- {spec['label']}: {format_value(key, get_field(prop, key), prop)}"
        if spec.get("publishable") is False:
            line += f" {INTERNAL_MARK}"
        lines.append(line)
    return f"{instructions}\n\n## נתוני הנכס\n" + "\n".join(lines) + "\n"


def main(argv):
    if len(argv) != 2:
        print("שימוש: python build_prompt.py <קובץ נכס>")
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    template = load_json(TEMPLATE_PATH)
    prop = load_json(argv[1])
    requests, problems = check(template, prop)
    if requests or problems:
        print("ההפקה נעצרה.")
        if requests:
            print("בקשת השלמה מרוכזת — יש למסור ערך או לאשר שאינו ידוע (בשדה כן/לא אפשר גם ״לא״):")
            for label in requests:
                print(f"- {label}")
        if problems:
            print("יש לתקן:")
            for p in problems:
                print(f"- {p}")
        return 1
    print(build_prompt(template, prop, load_instructions()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
