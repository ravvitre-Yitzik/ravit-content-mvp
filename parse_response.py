"""Read one provider's answer and check its structure: one non-empty post and exactly five non-empty hooks.

No network calls, no retries, no text is added or fixed.
Run: python parse_response.py <answer file>
"""
import json
import sys
from pathlib import Path

HOOKS_COUNT = 5


class ResponseError(Exception):
    """The answer is not in the required structure. .errors lists every problem found."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_response(text):
    """Return {"post": str, "hooks": [str, ...]} exactly as received, or raise ResponseError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ResponseError(["התשובה אינה JSON תקין (ייתכן שנוסף טקסט לפני או אחרי)"])

    if not isinstance(data, dict):
        raise ResponseError(["התשובה אינה אובייקט עם השדות post ו־hooks"])

    errors = []
    extra = sorted(set(data) - {"post", "hooks"})
    if extra:
        errors.append(f"שדות לא צפויים: {', '.join(extra)}")

    post = data.get("post")
    if "post" not in data:
        errors.append("חסר השדה post")
    elif not isinstance(post, str) or not post.strip():
        errors.append("הפוסט ריק או אינו טקסט")

    hooks = data.get("hooks")
    if "hooks" not in data:
        errors.append("חסר השדה hooks")
    elif not isinstance(hooks, list):
        errors.append("hooks אינו רשימה")
    else:
        if len(hooks) != HOOKS_COUNT:
            errors.append(f"נדרשים בדיוק {HOOKS_COUNT} משפטי שיתוף, התקבלו {len(hooks)}")
        for i, hook in enumerate(hooks, 1):
            if not isinstance(hook, str) or not hook.strip():
                errors.append(f"משפט שיתוף {i} ריק או אינו טקסט")

    if errors:
        raise ResponseError(errors)
    return {"post": post, "hooks": hooks}


def main(argv):
    if len(argv) != 2:
        print("שימוש: python parse_response.py <קובץ תשובה>")
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = parse_response(Path(argv[1]).read_text(encoding="utf-8"))
    except ResponseError as e:
        print("התשובה נדחתה:")
        for err in e.errors:
            print(f"- {err}")
        return 1
    print("התשובה תקינה.\n\nפוסט:")
    print(result["post"])
    print("\nמשפטי שיתוף:")
    for i, hook in enumerate(result["hooks"], 1):
        print(f"{i}. {hook}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
