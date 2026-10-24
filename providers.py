"""One generation call per provider with the same prompt. No automatic retries.

Each call returns a plain dict: the raw answer text, why it stopped, the usage the provider reported,
and the usage mapped for cost.py. Clients can be injected so tests run with fake answers only.
"""

REQUEST_TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 4000  # includes thinking tokens on both providers
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_THINKING_LEVEL = "low"  # lowest level documented for this model; thinking cannot be turned off
ANTHROPIC_MODEL = "claude-sonnet-5-5"
ANTHROPIC_THINKING = {"type": "between_tools"}  # lowest setting on this model: no up-front thinking

# Same JSON shape for both providers. "Exactly five hooks" is checked by parse_response,
# because Anthropic structured outputs do not support maxItems.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "post": {"type": "string"},
        "hooks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["post", "hooks"],
    "additionalProperties": False,
}


def _as_dict(obj):
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return dict(vars(obj))


def _result(provider, model, settings):
    return {
        "provider": provider,
        "model": model,
        "settings": settings,
        "ok": False,
        "text": None,
        "stop_reason": None,
        "truncated": False,
        "usage_raw": None,
        "usage": None,
        "error": None,
    }


def call_gemini(prompt, key, client=None):
    from google import genai
    from google.genai import types

    settings = {"max_output_tokens": MAX_OUTPUT_TOKENS, "thinking_level": GEMINI_THINKING_LEVEL,
                "response_mime_type": "application/json", "retries": 0}
    result = _result("Google", GEMINI_MODEL, settings)
    try:
        if client is None:
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_SECONDS * 1000,
                retry_options=types.HttpRetryOptions(attempts=1)))
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(thinking_level=GEMINI_THINKING_LEVEL),
                response_mime_type="application/json",
                response_json_schema=RESPONSE_SCHEMA,
            ),
        )
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e).replace(key, '***')}"
        return result

    candidates = getattr(response, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    result["stop_reason"] = getattr(reason, "name", reason)
    result["truncated"] = result["stop_reason"] == "MAX_TOKENS"
    result["text"] = getattr(response, "text", None)

    meta = getattr(response, "usage_metadata", None)
    result["usage_raw"] = _as_dict(meta)
    if meta is not None:
        thoughts = getattr(meta, "thoughts_token_count", None) or 0
        candidates_tokens = getattr(meta, "candidates_token_count", None)
        unpriced = []
        if getattr(meta, "cached_content_token_count", None):
            unpriced.append("טוקנים מהמטמון (cached_content_token_count)")
        if getattr(meta, "tool_use_prompt_token_count", None):
            unpriced.append("טוקני כלים (tool_use_prompt_token_count)")
        result["usage"] = {
            "model_id": GEMINI_MODEL,
            "input_tokens": getattr(meta, "prompt_token_count", None),
            # output is billed including thinking tokens
            "output_tokens": None if candidates_tokens is None else candidates_tokens + thoughts,
            "unpriced_components": unpriced,
        }
    result["ok"] = result["text"] is not None
    if not result["ok"]:
        result["error"] = f"לא התקבל טקסט (סיבת עצירה: {result['stop_reason']})"
    return result


def call_anthropic(prompt, key, client=None):
    import anthropic

    settings = {"max_tokens": MAX_OUTPUT_TOKENS, "thinking": ANTHROPIC_THINKING,
                "output_format": "json_schema", "retries": 0}
    result = _result("Anthropic", ANTHROPIC_MODEL, settings)
    try:
        if client is None:
            client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking=ANTHROPIC_THINKING,
            output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e).replace(key, '***')}"
        return result

    result["stop_reason"] = getattr(response, "stop_reason", None)
    result["truncated"] = result["stop_reason"] == "max_tokens"
    texts = [b.text for b in (getattr(response, "content", None) or []) if getattr(b, "type", None) == "text"]
    result["text"] = "".join(texts) if texts else None

    usage = getattr(response, "usage", None)
    result["usage_raw"] = _as_dict(usage)
    if usage is not None:
        unpriced = []
        if getattr(usage, "cache_creation_input_tokens", None):
            unpriced.append("כתיבה למטמון (cache_creation_input_tokens)")
        if getattr(usage, "cache_read_input_tokens", None):
            unpriced.append("קריאה מהמטמון (cache_read_input_tokens)")
        if getattr(usage, "server_tool_use", None):
            unpriced.append("כלי שרת (server_tool_use)")
        if getattr(usage, "inference_geo", None) not in (None, "global", "not_available"):
            unpriced.append(f"אזור הפעלה {usage.inference_geo}")
        result["usage"] = {
            "model_id": ANTHROPIC_MODEL,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),  # includes thinking tokens
            "unpriced_components": unpriced,
        }
    result["ok"] = result["text"] is not None
    if not result["ok"]:
        result["error"] = f"לא התקבל טקסט (סיבת עצירה: {result['stop_reason']})"
    return result
