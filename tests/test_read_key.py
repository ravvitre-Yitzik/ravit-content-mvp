import count_tokens_anthropic
import count_tokens_gemini
from build_prompt import TEMPLATE_PATH, build_prompt, load_instructions, load_json
from check_common import SAMPLE_PATH, read_key, sample_prompt


def test_reads_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=x\nGEMINI_API_KEY=test-value\n", encoding="utf-8")
    assert read_key("GEMINI_API_KEY", env) == "test-value"


def test_handles_bom_and_crlf(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("﻿GEMINI_API_KEY=test-value\r\n".encode("utf-8"))
    assert read_key("GEMINI_API_KEY", env) == "test-value"


def test_empty_or_missing_returns_empty(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    assert read_key("GEMINI_API_KEY", env) == ""
    assert read_key("GEMINI_API_KEY", tmp_path / "missing.env") == ""


def test_both_checks_use_the_same_sample_prompt():
    assert count_tokens_gemini.sample_prompt is sample_prompt
    assert count_tokens_anthropic.sample_prompt is sample_prompt
    expected = build_prompt(load_json(TEMPLATE_PATH), load_json(SAMPLE_PATH), load_instructions())
    assert sample_prompt() == expected
