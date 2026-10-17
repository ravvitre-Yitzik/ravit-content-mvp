"""Connection check: count the input tokens of the fictional sample prompt with Gemini. No text is generated.

Token counting is not billed according to Google's billing FAQ, and the project has no billing account.
The key is read from the local .env file and is never printed.
Run: python count_tokens_gemini.py
"""
import sys

from check_common import read_key, sample_prompt

MODEL = "gemini-3.8-flash"  # free-tier model, used for counting only; not yet chosen for generation


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    key = read_key("GEMINI_API_KEY")
    if not key:
        print("נכשל: לא נמצא ערך ל־GEMINI_API_KEY בקובץ .env")
        return 1
    print("המפתח נטען (הערך אינו מוצג).")
    prompt = sample_prompt()

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
