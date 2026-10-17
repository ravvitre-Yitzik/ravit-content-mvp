"""Connection check: count the input tokens of the fictional sample prompt with the Claude API. No text is generated.

Token counting is free to use according to Anthropic's docs, but the account needs a positive credit balance.
The key is read from the local .env file and is never printed. No automatic retries.
Run: python count_tokens_anthropic.py
"""
import sys

from check_common import read_key, sample_prompt

MODEL = "claude-sonnet-5-5"  # used for counting only; not yet chosen for generation


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    key = read_key("ANTHROPIC_API_KEY")
    if not key:
        print("נכשל: לא נמצא ערך ל־ANTHROPIC_API_KEY בקובץ .env")
        return 1
    print("המפתח נטען (הערך אינו מוצג).")
    prompt = sample_prompt()

    import anthropic

    client = anthropic.Anthropic(api_key=key, max_retries=0)
    try:
        result = client.messages.count_tokens(
            model=MODEL, messages=[{"role": "user", "content": prompt}]
        )
    except anthropic.APIStatusError as e:  # e.g. 401 authentication, 400 credit balance too low
        print(f"הבדיקה נכשלה: סטטוס {e.status_code}, {type(e).__name__}")
        print(str(e.message).replace(key, "***"))
        return 1
    except Exception as e:
        print(f"הבדיקה נכשלה: {type(e).__name__}: {str(e).replace(key, '***')}")
        return 1
    print(f"הבדיקה הצליחה. מודל לספירה: {MODEL}")
    print(f"טוקני קלט בבקשה של הנכס הבדיוני: {result.input_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
