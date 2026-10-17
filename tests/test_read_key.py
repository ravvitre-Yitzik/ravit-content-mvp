from count_tokens_gemini import read_key


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
