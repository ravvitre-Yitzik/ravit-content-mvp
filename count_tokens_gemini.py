"""Connection check: count the input tokens of the fictional sample prompt with Gemini. No text is generated.

Token counting is not billed according to Google's billing FAQ, and the project has no billing account.
The key is read from the local .env file and is never printed.
Run: python count_tokens_gemini.py
"""
import sys
from pathlib import Path

from build_prompt import ROOT, TEMPLATE_PATH, build_prompt, check, load_instructions, load_json

ENV_PATH = ROOT / ".env"
SAMPLE_PATH = ROOT / "data" / "sample-property.json"  # fictional only: the free tier may use content to improve Google's products
MODEL = "gemini-3.8-flash"  # free-tier model, used for counting only; not yet chosen for generation


def read_key(name, env_path=ENV_PATH):
    """Return the value of name in the .env file, or "" if missing."""
    if not Path(env_path).exists():
        return ""
    for line in Path(env_path).read_text(encoding="utf-8-sig").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    key = read_key("GEMINI_API_KEY")
    if not key:
        print("נכשל: לא נמצא ערך ל־GEMINI_API_KEY בקובץ .env")
        return 1
    print("המפתח נטען (הערך אינו מוצג).")

    template = load_json(TEMPLATE_PATH)
    prop = load_json(SAMPLE_PATH)
    if check(template, prop) != ([], []):
        print("נכשל: הנכס הבדיוני אינו תקין לפי התבנית")
        return 1
    prompt = build_prompt(template, prop, load_instructions())

    from google import genai

    try:
        client = genai.Client(api_key=key)  # keep a reference: an unreferenced client is closed before the request
        result = client.models.count_tokens(model=MODEL, contents=prompt)
    except Exception as e:  # report the error without the key
        print(f"החיבור נכשל: {type(e).__name__}: {str(e).replace(key, '***')}")
        return 1
    print(f"החיבור הצליח. מודל לספירה: {MODEL}")
    print(f"טוקני קלט בבקשה של הנכס הבדיוני: {result.total_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
