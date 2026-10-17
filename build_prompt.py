"""Load the template, a property file and the writing instructions; validate; build one shared prompt.

No network calls. Run: python build_prompt.py data/sample-property.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TEMPLATE_PATH = ROOT / "data" / "property-template.json"
INSTRUCTIONS_PATH = ROOT / "instructions" / "course-writing-instructions.md"

STATUSES = {"known", "unknown", "not_existing", "conflicting"}
STATUS_TEXT = {"unknown": "לא ידוע", "not_existing": "לא קיים"}
INTERNAL_MARK = "[פנימי, לא לפרסום]"  # same wording as the privacy section in the instructions


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_instructions(path=INSTRUCTIONS_PATH):
    """Instructions text without the leading source comment (meant for humans, not providers)."""
    text = Path(path).read_text(encoding="utf-8")
    return re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.DOTALL).strip()


def is_empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def validate(template, prop):
    """Return a list of problems in Hebrew. An empty list means production may start."""
    errors = []
    tfields = template["fields"]
    pfields = prop.get("fields", {})

    for key in pfields:
        if key not in tfields:
            errors.append(f"שדה לא מוכר בתבנית: {key}")

    for key, spec in tfields.items():
        label = spec["label"]
        field = pfields.get(key)
        if field is None:
            if spec["required"]:
                errors.append(f"{label}: שדה חובה חסר בקובץ")
            continue

        status = field.get("status")
        value = field.get("value")
        if status not in STATUSES:
            errors.append(f"{label}: מצב לא חוקי '{status}'")
            continue
        if status == "conflicting":
            errors.append(f"{label}: נתונים סותרים, יש לתקן לפני הפקה")
            continue
        if spec["required"] and (status != "known" or is_empty(value)):
            errors.append(f"{label}: שדה חובה, נדרש ערך ממשי (כעת: {STATUS_TEXT.get(status, 'ריק')})")
            continue
        if status == "known":
            if is_empty(value):
                errors.append(f"{label}: מסומן ידוע אך אין ערך")
            elif "allowed_values" in spec and value not in spec["allowed_values"]:
                errors.append(f"{label}: ערך לא מותר '{value}'")

    return errors


def format_value(key, field, prop):
    status = field["status"]
    if status in STATUS_TEXT:
        return STATUS_TEXT[status]
    value = field["value"]
    if value is True:
        return "יש"
    if value is False:
        return "אין"
    if key == "price":
        deal = prop["fields"]["deal_type"]["value"]
        return f"{value} ₪ לחודש" if deal == "השכרה" else f"{value} ₪ מחיר כולל"
    return str(value)


def build_prompt(template, prop, instructions):
    """One prompt, identical for every provider. Call validate() first."""
    lines = []
    for key, spec in template["fields"].items():
        field = prop["fields"].get(key, {"status": "unknown", "value": None})
        line = f"- {spec['label']}: {format_value(key, field, prop)}"
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
    errors = validate(template, prop)
    if errors:
        print("ההפקה נעצרה. יש לתקן:")
        for e in errors:
            print(f"- {e}")
        return 1
    print(build_prompt(template, prop, load_instructions()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
