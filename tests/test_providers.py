"""Provider calls and the first run, with fake clients only. No network."""
import json
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

import providers
import run_generation
from check_common import sample_prompt

KEY = "fake-key-123"
GOOD = json.dumps({"post": "פוסט בדיוני.\nרוית קרודו | רווית נכסים",
                   "hooks": ["א", "ב", "ג", "ד", "ה"]}, ensure_ascii=False)


class FakeGemini:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeAnthropic:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def gemini_response(text=GOOD, finish="STOP", cached=None, thoughts=300):
    return NS(text=text, candidates=[NS(finish_reason=NS(name=finish))],
              usage_metadata=NS(prompt_token_count=2291, candidates_token_count=900, thoughts_token_count=thoughts,
                                cached_content_token_count=cached, tool_use_prompt_token_count=None))


def anthropic_response(text=GOOD, stop="end_turn", cache_read=0):
    return NS(content=[NS(type="text", text=text)], stop_reason=stop,
              usage=NS(input_tokens=3318, output_tokens=1500, cache_creation_input_tokens=0,
                       cache_read_input_tokens=cache_read, server_tool_use=None, inference_geo="global"))


# --- Google ---

def test_gemini_request_settings_and_usage():
    client = FakeGemini(gemini_response())
    r = providers.call_gemini("בקשה", KEY, client)
    call = client.calls[0]
    assert call["model"] == "gemini-3.8-flash" and call["contents"] == "בקשה"
    assert call["config"].max_output_tokens == 4000
    assert call["config"].thinking_config.thinking_level.name == "LOW"
    assert call["config"].response_mime_type == "application/json"
    assert r["ok"] and r["text"] == GOOD and r["stop_reason"] == "STOP" and not r["truncated"]
    assert r["usage"] == {"model_id": "gemini-3.8-flash", "input_tokens": 2291,
                          "output_tokens": 1200, "unpriced_components": []}  # 900 + 300 thinking
    assert r["usage_raw"]["thoughts_token_count"] == 300


def test_gemini_truncated():
    r = providers.call_gemini("בקשה", KEY, FakeGemini(gemini_response(text='{"post": "חת', finish="MAX_TOKENS")))
    assert r["truncated"]


def test_gemini_cached_tokens_are_flagged():
    r = providers.call_gemini("בקשה", KEY, FakeGemini(gemini_response(cached=1024)))
    assert r["usage"]["unpriced_components"] == ["טוקנים מהמטמון (cached_content_token_count)"]


def test_gemini_error_hides_key():
    r = providers.call_gemini("בקשה", KEY, FakeGemini(error=RuntimeError(f"bad key {KEY}")))
    assert not r["ok"] and KEY not in r["error"] and "***" in r["error"]


def test_gemini_client_built_without_retries(monkeypatch):
    built = {}

    def fake_client(**kwargs):
        built.update(kwargs)
        return FakeGemini(gemini_response())

    monkeypatch.setattr("google.genai.Client", fake_client)
    providers.call_gemini("בקשה", KEY)
    assert built["api_key"] == KEY and built["http_options"].retry_options.attempts == 1


# --- Anthropic ---

def test_anthropic_request_settings_and_usage():
    client = FakeAnthropic(anthropic_response())
    r = providers.call_anthropic("בקשה", KEY, client)
    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["max_tokens"] == 4000
    assert call["thinking"] == {"type": "between_tools"}
    assert call["output_config"]["format"]["schema"] == providers.RESPONSE_SCHEMA
    assert call["messages"] == [{"role": "user", "content": "בקשה"}]
    assert r["ok"] and r["text"] == GOOD and r["stop_reason"] == "end_turn"
    assert r["usage"] == {"model_id": "claude-sonnet-5-5", "input_tokens": 3318,
                          "output_tokens": 1500, "unpriced_components": []}


def test_anthropic_truncated_and_cache_flagged():
    r = providers.call_anthropic("בקשה", KEY, FakeAnthropic(anthropic_response(stop="max_tokens", cache_read=50)))
    assert r["truncated"]
    assert r["usage"]["unpriced_components"] == ["קריאה מהמטמון (cache_read_input_tokens)"]


def test_anthropic_error_hides_key():
    r = providers.call_anthropic("בקשה", KEY, FakeAnthropic(error=RuntimeError(f"denied {KEY}")))
    assert not r["ok"] and KEY not in r["error"]


def test_anthropic_client_built_without_retries(monkeypatch):
    built = {}

    def fake_client(**kwargs):
        built.update(kwargs)
        return FakeAnthropic(anthropic_response())

    monkeypatch.setattr("anthropic.Anthropic", fake_client)
    providers.call_anthropic("בקשה", KEY)
    assert built == {"api_key": KEY, "max_retries": 0}


# --- structure check and cost ---

RATES = run_generation.load_rates()


def test_valid_answers_pass_structure_and_cost():
    g = run_generation.evaluate(providers.call_gemini("p", KEY, FakeGemini(gemini_response())), RATES)
    a = run_generation.evaluate(providers.call_anthropic("p", KEY, FakeAnthropic(anthropic_response())), RATES)
    assert g["parsed"]["hooks"] == ["א", "ב", "ג", "ד", "ה"] and g["structure_errors"] == []
    assert g["cost"]["available"] and g["cost"]["total"] == 0                      # free tier rate
    # 3318 * 2 / 1M + 1500 * 10 / 1M = 0.006636 + 0.015
    assert a["cost"]["available"] and a["cost"]["total"] == Decimal("0.021636")


def test_invalid_answer_is_rejected_not_fixed():
    bad = json.dumps({"post": "פוסט", "hooks": ["א", "ב"]}, ensure_ascii=False)
    r = run_generation.evaluate(providers.call_anthropic("p", KEY, FakeAnthropic(anthropic_response(text=bad))), RATES)
    assert r["parsed"] is None and r["structure_errors"] == ["נדרשים בדיוק 5 משפטי שיתוף, התקבלו 2"]


def test_truncation_is_reported():
    r = run_generation.evaluate(
        providers.call_gemini("p", KEY, FakeGemini(gemini_response(text='{"post": "ח', finish="MAX_TOKENS"))), RATES)
    assert r["structure_errors"][0] == "התשובה נקטעה במגבלת הפלט"


def test_unpriced_component_makes_cost_unavailable():
    r = run_generation.evaluate(providers.call_gemini("p", KEY, FakeGemini(gemini_response(cached=1024))), RATES)
    assert not r["cost"]["available"] and r["cost"]["total"] is None
    assert "רכיב חיוב שאינו מחושב: טוקנים מהמטמון (cached_content_token_count)" in r["cost"]["reasons"]


def test_failed_call_has_unavailable_cost():
    r = run_generation.evaluate(providers.call_gemini("p", KEY, FakeGemini(error=RuntimeError("down"))), RATES)
    assert not r["cost"]["available"] and r["parsed"] is None


# --- the run ---

def test_plan_only_without_run_flag(capsys):
    called = []
    assert run_generation.main(["run_generation.py"], lambda *a: called.append(a), lambda *a: called.append(a)) == 0
    assert called == [] and "לא בוצעה קריאה" in capsys.readouterr().out


def test_run_sends_same_prompt_to_both_and_saves(monkeypatch):
    prompts = []

    def fake_gemini(prompt, key):
        prompts.append(prompt)
        return providers.call_gemini(prompt, key, FakeGemini(gemini_response()))

    def fake_anthropic(prompt, key):
        prompts.append(prompt)
        return providers.call_anthropic(prompt, key, FakeAnthropic(anthropic_response()))

    saved = []
    monkeypatch.setattr(run_generation, "read_key", lambda name: KEY)
    monkeypatch.setattr(run_generation, "save_run", lambda run: saved.append(run) or run_generation.RUNS_DIR / "test.json")
    assert run_generation.main(["run_generation.py", "--run"], fake_gemini, fake_anthropic) == 0
    assert prompts == [sample_prompt(), sample_prompt()]
    run = saved[0]
    assert run["total_cost"]["available"] and run["total_cost"]["total"] == Decimal("0.021636")
    assert KEY not in json.dumps(run, ensure_ascii=False, default=str)


def test_save_run_writes_json(tmp_path):
    path = run_generation.save_run({"run_id": "t1", "amount": Decimal("0.5")}, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"run_id": "t1", "amount": "0.5"}
