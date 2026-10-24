"""Grading and the summary log, on fake runs in a temporary folder. No network."""
import csv
import hashlib
import json

import pytest

import grading
import run_log

RATE = {"model_id": "m-b", "currency": "USD", "unit": "per_1m_tokens", "input_price": "2", "output_price": "10",
        "as_of": "2026-10-17", "source": "test"}


def result(provider, model, ok=True, structure_errors=None, cost_total="0.01", available=True, rate=RATE):
    return {"provider": provider, "model": model, "ok": ok, "error": None if ok else "down",
            "structure_errors": structure_errors or [], "parsed": {"post": "פוסט", "hooks": ["1", "2", "3", "4", "5"]} if ok else None,
            "usage": {"model_id": model, "input_tokens": 100, "output_tokens": 50} if ok else None,
            "cost": {"available": available, "total": cost_total if available else None, "currency": "USD", "reasons": []},
            "rate": rate, "stop_reason": "end", "truncated": False, "text": "{}" if ok else None, "settings": {}}


def write_run(runs_dir, run_id, results, total="0.01", available=True):
    run = {"run_id": run_id, "date": "2026-10-17T22:00:00", "prompt": "בקשה", "results": results,
           "total_cost": {"available": available, "total": total if available else None, "currency": "USD", "reasons": []}}
    path = runs_dir / f"{run_id}.json"
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    return path, run


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(run_log, "RUNS_DIR", tmp_path)
    return tmp_path


def two_ok(runs_dir, run_id="r1"):
    return write_run(runs_dir, run_id, [result("Google", "m-a", rate={**RATE, "model_id": "m-a", "input_price": "0", "output_price": "0"}),
                                        result("Anthropic", "m-b")])


# --- grading ---

def test_mapping_follows_run_order(runs):
    _, run = two_ok(runs)
    assert [(label, r["provider"]) for label, r in grading.packages(run)] == [("א", "Google"), ("ב", "Anthropic")]


def test_save_grading_links_to_run_and_keeps_run_unchanged(runs):
    path, run = two_ok(runs)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    gpath = grading.save_grading(path, run, {"א": 3, "ב": 4}, "ב", "הערה")
    saved = json.loads(gpath.read_text(encoding="utf-8"))
    assert gpath.name == "r1.grading.json" and saved["run_id"] == "r1"
    assert saved["mapping"]["א"] == {"provider": "Google", "model": "m-a"}
    assert saved["scores"] == {"א": 3, "ב": 4} and saved["choice"] == "ב" and saved["publication_approval"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_no_duplicate_grading(runs):
    path, run = two_ok(runs)
    grading.save_grading(path, run, {"א": 3, "ב": 3}, "אף אחת")
    with pytest.raises(FileExistsError):
        grading.save_grading(path, run, {"א": 5, "ב": 5}, "א")


@pytest.mark.parametrize("scores, choice, problem", [
    ({"א": 0, "ב": 3}, "ב", "נדרש ציון שלם בין 1 ל־5"),
    ({"א": 6, "ב": 3}, "ב", "נדרש ציון שלם בין 1 ל־5"),
    ({"א": "3", "ב": 3}, "ב", "נדרש ציון שלם בין 1 ל־5"),
    ({"א": 3, "ב": 3}, "ג", "בחירה לא תקפה"),
])
def test_invalid_grading_rejected(runs, scores, choice, problem):
    _, run = two_ok(runs)
    assert any(problem in e for e in grading.check_grading(run, scores, choice))


def test_failed_package_gets_no_score_and_cannot_be_chosen(runs):
    _, run = write_run(runs, "r2", [result("Google", "m-a", ok=False, available=False), result("Anthropic", "m-b")])
    assert grading.check_grading(run, {"ב": 3}, "ב") == []
    assert "לא מקבלת ציון" in grading.check_grading(run, {"א": 2, "ב": 3}, "ב")[0]
    assert "בחירה לא תקפה" in grading.check_grading(run, {"ב": 3}, "א")[0]


def test_interactive_grading(runs, capsys):
    two_ok(runs)
    answers = iter(["7", "3", "4", "ג", "ב׳", "בסיס לעריכה"])
    assert grading.main(["grading.py", "r1"], ask=lambda _: next(answers)) == 0
    saved = json.loads((runs / "r1.grading.json").read_text(encoding="utf-8"))
    assert saved["scores"] == {"א": 3, "ב": 4} and saved["choice"] == "ב" and saved["note"] == "בסיס לעריכה"
    out = capsys.readouterr().out
    assert "א׳ = Google · m-a" in out and "הבחירה אינה אישור לפרסום" in out
    assert (runs / "log.csv").exists()


def test_existing_grading_is_shown_not_duplicated(runs, capsys):
    path, run = two_ok(runs)
    grading.save_grading(path, run, {"א": 3, "ב": 3}, "ב")
    before = (runs / "r1.grading.json").read_bytes()
    assert grading.main(["grading.py", "r1"], ask=lambda _: pytest.fail("must not ask")) == 0
    assert "כבר יש דירוג" in capsys.readouterr().out
    assert (runs / "r1.grading.json").read_bytes() == before


def test_bad_run_id(runs, capsys):
    assert grading.main(["grading.py", "../secret"]) == 1
    assert grading.main(["grading.py", "missing"]) == 1


# --- log ---

def read_log(runs_dir):
    with (runs_dir / "log.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_log_rows_with_rates_scores_and_link(runs):
    path, run = two_ok(runs)
    grading.save_grading(path, run, {"א": 3, "ב": 4}, "ב", "הערה")
    run_log.write_log()
    rows = read_log(runs)
    assert [(r["label"], r["provider"], r["score"], r["choice"]) for r in rows] == [("א", "Google", "3", "ב"), ("ב", "Anthropic", "4", "ב")]
    b = rows[1]
    assert (b["input_price"], b["output_price"], b["unit"], b["currency"], b["rate_date"]) == ("2", "10", "per_1m_tokens", "USD", "2026-10-17")
    assert b["call_cost"] == "0.01" and b["full_output"] == "runs/r1.json" and b["status"] == "תקין"
    assert rows[0]["input_price"] == "0"   # a real zero stays zero


def test_log_missing_data_is_unavailable_not_zero(runs):
    write_run(runs, "old", [result("Google", "m-a", rate=None), result("Anthropic", "m-b", ok=False, available=False, rate=None)],
              available=False)
    rows = read_log(runs) if run_log.write_log() else None
    for r in rows:
        assert r["input_price"] == r["rate_date"] == r["score"] == r["choice"] == "לא זמין"
        assert r["comparison_cost"] == "לא זמין"
    assert rows[1]["call_cost"] == "לא זמין" and rows[1]["status"] == "נכשל"
    assert rows[1]["input_tokens"] == "לא זמין"


def test_log_is_rebuilt_without_duplicates(runs):
    two_ok(runs)
    run_log.write_log()
    run_log.write_log()
    assert len(read_log(runs)) == 2


def test_log_neutralizes_spreadsheet_formulas(runs):
    failed = result("Google", "m-a", ok=False, available=False)
    failed["error"] = "=HYPERLINK(\"http://x\")"
    path, run = write_run(runs, "r3", [failed, result("Anthropic", "m-b")])
    grading.save_grading(path, run, {"ב": 2}, "ב", "+cmd")
    run_log.write_log()
    rows = read_log(runs)
    assert rows[0]["structure_errors"].startswith("'=")
    assert rows[1]["note"] == "'+cmd"


def test_grading_files_are_not_read_as_runs(runs):
    path, run = two_ok(runs)
    grading.save_grading(path, run, {"א": 1, "ב": 2}, "אף אחת")
    assert [p.name for p in run_log.run_files(runs)] == ["r1.json"]


def test_nested_ui_log_links_to_actual_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_log, "ROOT", tmp_path)
    nested = tmp_path / "runs" / "ui-live"
    nested.mkdir(parents=True)
    path, _ = two_ok(nested)
    rows = run_log.rows_for_run(path)
    assert rows[0]["full_output"] == "runs/ui-live/r1.json"
    assert (tmp_path / rows[0]["full_output"]).is_file()


def test_log_rebuild_failure_preserves_previous_complete_csv(runs, monkeypatch):
    two_ok(runs)
    path = run_log.write_log()
    original = path.read_bytes()
    def fail(_path):
        raise OSError("read interrupted")
    monkeypatch.setattr(run_log, "rows_for_run", fail)
    with pytest.raises(OSError):
        run_log.write_log()
    assert path.read_bytes() == original
    assert not list(runs.glob(".log-*.tmp"))
