"""HTTP boundary tests without sockets; the existing network block stays enabled."""
import io
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

import local_ui
from comparison_flow import DemoComparisonService, InputValidationError


class FakeService:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def compare(self, prop, request_id):
        self.calls.append(("compare", prop, request_id))
        if self.error:
            raise self.error
        return {"run": {"mode": "demo", "run_id": "demo-id", "results": []},
                "grading": None, "already_generated": False}

    def grade(self, run_id, scores, choice, note=""):
        self.calls.append(("grade", run_id, scores, choice, note))
        if self.error:
            raise self.error
        return {"run_id": run_id, "grading": {"scores": scores, "choice": choice, "note": note},
                "already_saved": False}


def request(service, method="GET", path="/", payload=None, *, body=None, headers=None, remove=(),
            allow_live=False, live_service=None, allow_work=False, work_service=None):
    handler_type = local_ui.create_handler(service, allow_live=allow_live, live_service=live_service,
                                            allow_work=allow_work, work_service=work_service)
    handler = handler_type.__new__(handler_type)
    handler.server = SimpleNamespace(server_address=("127.0.0.1", 8765))
    handler.request_version = "HTTP/1.1"
    handler.requestline = method + " " + path + " HTTP/1.1"
    handler.command = method
    handler.path = path
    handler.client_address = ("127.0.0.1", 1)
    raw = body if body is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
    handler.headers = Message()
    defaults = {"Host": "127.0.0.1:8765"}
    if method == "POST":
        defaults.update({"Origin": "http://127.0.0.1:8765", "Content-Type": "application/json", "Content-Length": str(len(raw))})
    defaults.update(headers or {})
    for key, value in defaults.items():
        if key not in remove:
            if isinstance(value, list):
                for item in value:
                    handler.headers.add_header(key, item)
            else:
                handler.headers.add_header(key, value)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    getattr(handler, "do_" + method)()
    head, result = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    lines = head.decode("latin1").split("\r\n")
    status = int(lines[0].split()[1])
    response_headers = dict(line.split(": ", 1) for line in lines[1:])
    return status, response_headers, result


COMPARE = {"property": {"fields": {}}, "request_id": "request-one"}
GRADE = {"run_id": "demo-id", "scores": {"א": 3, "ב": 4}, "choice": "ב", "note": "הערה"}


def test_schema_returns_only_template_and_fictional_sample_without_service_calls():
    service = FakeService()
    status, headers, body = request(service, path="/api/schema")
    data = json.loads(body)
    assert status == 200 and set(data) == {"template", "sample", "mode", "modes"}
    assert data["mode"] == "demo" and data["sample"]["example_id"] == "fictional-sample"
    assert "ravit_impression" in data["template"]["fields"]
    assert service.calls == []
    assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path,mime", [("/", "text/html"), ("/app.css", "text/css"), ("/app.js", "text/javascript")])
def test_only_known_assets_are_served_with_browser_protections(path, mime):
    status, headers, body = request(FakeService(), path=path)
    assert status == 200 and headers["Content-Type"].startswith(mime) and body
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("path", ["/.env", "/runs/log.csv", "/runs/demo/test.json", "/private/x",
                                 "/source-materials/x", "/../README.md", "/%2e%2e/.env",
                                 "/web/../.env", "/api/unknown", "/app.js/../.env", "/?file=.env"])
def test_secret_and_traversal_routes_are_never_served(path):
    status, _, body = request(FakeService(), path=path)
    assert status == 404
    assert set(json.loads(body)) == {"error"}
    assert b"Traceback" not in body


@pytest.mark.parametrize("host", ["evil.test:8765", "127.0.0.1:9999", "127.0.0.1:8765.evil.test",
                                 "user@127.0.0.1:8765", ["127.0.0.1:8765", "evil.test"]])
def test_host_validation_blocks_rebinding_and_duplicate_headers(host):
    assert request(FakeService(), path="/api/schema", headers={"Host": host})[0] == 403


def test_host_is_required_even_for_static_get():
    assert request(FakeService(), remove=("Host",))[0] == 403


@pytest.mark.parametrize("origin", ["https://evil.test", "null", "http://127.0.0.1:9999",
                                   "http://127.0.0.1:8765.evil", ["http://127.0.0.1:8765", "null"]])
def test_cross_origin_posts_do_not_call_service(origin):
    service = FakeService()
    assert request(service, "POST", "/api/compare", COMPARE, headers={"Origin": origin})[0] == 403
    assert service.calls == []


def test_origin_is_required_for_posts_and_no_cors_preflight_is_supported():
    assert request(FakeService(), "POST", "/api/compare", COMPARE, remove=("Origin",))[0] == 403
    assert request(FakeService(), "OPTIONS", "/api/compare")[0] == 405


def test_cross_site_fetch_metadata_is_rejected():
    assert request(FakeService(), headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_localhost_with_matching_port_is_allowed():
    headers = {"Host": "localhost:8765", "Origin": "http://localhost:8765"}
    assert request(FakeService(), "POST", "/api/compare", COMPARE, headers=headers)[0] == 200


@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b"\xff", b'{"x": NaN}', b"[" * 1100 + b"]" * 1100])
def test_malformed_json_is_400_and_never_calls_service(body):
    service = FakeService()
    assert request(service, "POST", "/api/compare", body=body)[0] == 400
    assert service.calls == []


@pytest.mark.parametrize("headers,remove,expected", [
    ({"Content-Length": str(local_ui.MAX_BODY + 1)}, (), 413),
    ({"Content-Length": "-1"}, (), 400),
    ({"Content-Length": "8"}, (), 400),
    ({"Content-Length": ["2", "2"]}, (), 400),
    ({"Transfer-Encoding": "chunked"}, (), 400),
    ({"Content-Type": "text/plain"}, (), 415),
    ({"Content-Type": ["application/json", "text/plain"]}, (), 415),
    ({}, ("Content-Length",), 400),
    ({}, ("Content-Type",), 415),
])
def test_body_boundaries(headers, remove, expected):
    service = FakeService()
    assert request(service, "POST", "/api/compare", body=b"{}", headers=headers, remove=remove)[0] == expected
    assert service.calls == []


@pytest.mark.parametrize("path,payload", [
    ("/api/compare", {}), ("/api/compare", {"property": [], "request_id": "one"}),
    ("/api/compare", {**COMPARE, "live": True}), ("/api/compare", {**COMPARE, "request_id": ""}),
    ("/api/grade", {}), ("/api/grade", {**GRADE, "scores": []}),
    ("/api/grade", {**GRADE, "note": {}}), ("/api/grade", {**GRADE, "run_id": ""}),
])
def test_bad_request_shapes_are_400(path, payload):
    assert request(FakeService(), "POST", path, payload)[0] == 400


@pytest.mark.parametrize("error,expected", [
    (InputValidationError(["קומה"], ["נתונים סותרים"]), 422),
    (ValueError("internal detail"), 409), (FileExistsError("private path"), 409),
    (FileNotFoundError("private path"), 404), (RuntimeError("SECRET_MARKER"), 500),
])
def test_domain_errors_are_mapped_without_internal_information(error, expected):
    status, _, body = request(FakeService(error), "POST", "/api/compare", COMPARE)
    data = json.loads(body)
    assert status == expected and "error" in data
    assert b"SECRET_MARKER" not in body and b"private path" not in body and b"internal detail" not in body
    if expected == 422:
        assert data["completion_requests"] == ["קומה"] and data["problems"] == ["נתונים סותרים"]


def test_grade_dispatch_and_unknown_post():
    service = FakeService()
    assert request(service, "POST", "/api/grade", GRADE)[0] == 200
    assert service.calls == [("grade", "demo-id", {"א": 3, "ב": 4}, "ב", "הערה")]
    assert request(service, "POST", "/api/missing", {})[0] == 404
    assert len(service.calls) == 1


def test_actual_demo_flow_roundtrips_and_retries_without_duplicate_files(tmp_path):
    service = DemoComparisonService(tmp_path)
    sample = json.loads((local_ui.ROOT / "data" / "sample-property.json").read_text(encoding="utf-8"))
    payload = {"property": sample, "request_id": "browser-request"}
    first = json.loads(request(service, "POST", "/api/compare", payload)[2])
    second = json.loads(request(service, "POST", "/api/compare", payload)[2])
    assert first["run"]["mode"] == "demo" and second["already_generated"] is True
    assert first["run"]["run_id"] == second["run"]["run_id"]
    assert all(len(result["parsed"]["hooks"]) == 5 for result in first["run"]["results"])
    grade = {**GRADE, "run_id": first["run"]["run_id"]}
    saved = json.loads(request(service, "POST", "/api/grade", grade)[2])
    repeated = json.loads(request(service, "POST", "/api/grade", grade)[2])
    assert saved["grading"]["publication_approval"] is False and repeated["already_saved"] is True
    assert len(list(tmp_path.glob("*.grading.json"))) == 1
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert request(service, "POST", "/api/grade", {**grade, "choice": "א"})[0] == 409


def test_api_carries_hostile_text_as_json_and_frontend_has_no_html_injection_sinks():
    hostile = '<img src=x onerror="alert(1)">'
    payload = {**GRADE, "note": hostile}
    status, headers, body = request(FakeService(), "POST", "/api/grade", payload)
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["grading"]["note"] == hostile
    source = (local_ui.ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for unsafe_sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert unsafe_sink not in source
    assert "node.textContent = text" in source
    html = (local_ui.ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'dir="rtl"' in html and 'aria-live="polite"' in html
    assert "<script src=\"/app.js\" defer>" in html


def test_server_factory_always_binds_to_loopback_without_constructing_real_socket(monkeypatch):
    captured = []
    sentinel = object()
    monkeypatch.setattr(local_ui, "HTTPServer", lambda address, handler: captured.append(address) or sentinel)
    assert local_ui.make_server(8769, FakeService()) is sentinel
    assert captured == [("127.0.0.1", 8769)]


class FakeLiveService(FakeService):
    def compare(self, prop, request_id):
        result = super().compare(prop, request_id)
        result["run"]["mode"] = "live"
        return result

    def get_by_request(self, request_id):
        self.calls.append(("get_by_request", request_id))
        if self.error:
            raise self.error
        return {"run": {"mode": "live", "run_id": "12345678-1234-1234-1234-123456789abc",
                        "request_id": request_id, "results": []}, "grading": None}

    def get_run(self, run_id):
        self.calls.append(("get_run", run_id))
        if self.error:
            raise self.error
        return {"run": {"mode": "live", "run_id": run_id, "results": []}, "grading": None}


LIVE = {**COMPARE, "mode": "live", "live_confirmed": True}
RUN_ID = "12345678-1234-1234-1234-123456789abc"


def test_live_metadata_is_safe_and_disabled_by_default():
    live = FakeLiveService()
    data = json.loads(request(FakeService(), path="/api/schema", live_service=live)[2])
    assert data["mode"] == "demo" and data["modes"]["live"]["available"] is False
    assert data["modes"]["live"]["max_total_usd"] == "0.50"
    assert data["modes"]["live"]["max_total_runs"] == 1
    assert data["modes"]["live"]["sample_only"] is True
    assert live.calls == []
    enabled = json.loads(request(FakeService(), path="/api/schema", allow_live=True, live_service=live)[2])
    assert enabled["modes"]["live"]["available"] is True and live.calls == []


@pytest.mark.parametrize("allow,service_present", [(False, False), (False, True), (True, False)])
def test_live_requires_explicit_server_gate_and_service(allow, service_present):
    live = FakeLiveService()
    demo = FakeService()
    status, _, body = request(demo, "POST", "/api/compare", LIVE, allow_live=allow,
                              live_service=live if service_present else None)
    assert status == 403 and json.loads(body)["retry_allowed"] is False
    assert live.calls == demo.calls == []


@pytest.mark.parametrize("confirmation", [None, False, 1, "true", [], {}])
def test_live_compare_requires_literal_browser_confirmation(confirmation):
    live = FakeLiveService()
    assert request(FakeService(), "POST", "/api/compare", {**LIVE, "live_confirmed": confirmation},
                   allow_live=True, live_service=live)[0] == 403
    assert live.calls == []


@pytest.mark.parametrize("mode", ["unknown", None, {}, []])
def test_unknown_modes_cannot_trigger_either_service(mode):
    demo, live = FakeService(), FakeLiveService()
    assert request(demo, "POST", "/api/compare", {**LIVE, "mode": mode},
                   allow_live=True, live_service=live)[0] == 400
    assert demo.calls == live.calls == []


def test_explicit_live_dispatch_and_default_demo_remain_separate():
    demo, live = FakeService(), FakeLiveService()
    assert request(demo, "POST", "/api/compare", LIVE, allow_live=True, live_service=live)[0] == 200
    assert live.calls == [("compare", COMPARE["property"], COMPARE["request_id"])] and demo.calls == []
    assert request(demo, "POST", "/api/compare", COMPARE, allow_live=True, live_service=live)[0] == 200
    assert len(demo.calls) == 1 and len(live.calls) == 1
    assert request(demo, "POST", "/api/grade", {**GRADE, "mode": "live"},
                   allow_live=True, live_service=live)[0] == 200
    assert live.calls[-1][0] == "grade"


@pytest.mark.parametrize("error,status", [(ValueError("SECRET"), 409), (RuntimeError("SECRET"), 500)])
def test_live_error_never_claims_no_call_and_forbids_retry(error, status):
    service = FakeLiveService(error)
    code, _, body = request(FakeService(), "POST", "/api/compare", LIVE, allow_live=True, live_service=service)
    data = json.loads(body)
    assert code == status and data["retry_allowed"] is False
    assert "אין להפעיל שוב" in data["error"]
    assert "לא נשלחה בקשה" not in data["error"] and "SECRET" not in data["error"]


def test_saved_live_run_load_is_read_only_and_does_not_compare():
    demo, live = FakeService(), FakeLiveService()
    path = "/api/run?mode=live&run_id=" + RUN_ID
    status, _, body = request(demo, path=path, allow_live=True, live_service=live)
    assert status == 200 and json.loads(body)["run"]["run_id"] == RUN_ID
    assert live.calls == [("get_run", RUN_ID)] and demo.calls == []
    assert request(demo, path="/?mode=live&run_id=" + RUN_ID, allow_live=True, live_service=live)[0] == 200
    assert len(live.calls) == 1  # Serving the screen never triggers even a stored-run load.
    assert request(demo, path=path, live_service=live)[0] == 403


@pytest.mark.parametrize("query", ["", "?mode=demo&run_id=" + RUN_ID, "?mode=live&run_id=../.env",
                                  "?mode=live&run_id=%2e%2e%2f.env", "?mode=live&run_id=not-a-uuid",
                                  "?mode=live&run_id=" + RUN_ID + "&path=.env",
                                  "?mode=live&run_id=" + RUN_ID + "&run_id=" + RUN_ID])
def test_saved_run_query_cannot_select_paths_or_private_directories(query):
    service = FakeLiveService()
    assert request(FakeService(), path="/api/run" + query, allow_live=True, live_service=service)[0] == 400
    assert service.calls == []


def test_saved_run_failures_hide_paths_and_exception_details():
    for error, expected in [(FileNotFoundError("SECRET"), 404), (ValueError("SECRET"), 400), (RuntimeError("SECRET"), 500)]:
        status, _, body = request(FakeService(), path="/api/run?mode=live&run_id=" + RUN_ID,
                                  allow_live=True, live_service=FakeLiveService(error))
        assert status == expected and b"SECRET" not in body


def test_server_live_opt_in_constructs_fixed_single_run_budget_without_calls(monkeypatch):
    import comparison_flow
    constructed = []
    live = FakeLiveService()
    monkeypatch.setattr(comparison_flow, "LiveComparisonService", lambda **kwargs: constructed.append(kwargs) or live)
    monkeypatch.setattr(local_ui, "HTTPServer", lambda address, handler: (address, handler))
    local_ui.make_server(8765, FakeService())
    assert constructed == []
    local_ui.make_server(8765, FakeService(), allow_live=True)
    assert constructed == [{"enabled": True, "max_cost_usd": "0.50", "max_total_runs": 1}]
    assert live.calls == []


def test_live_controls_are_explicit_and_no_mode_switch_autogenerates():
    source = (local_ui.ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = (local_ui.ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="live-confirmed" type="checkbox"' in html
    assert 'id="live-option" value="live" disabled' in html
    assert "structuredClone(state.reviewProperty)" in source
    assert 'remember("ravit-ui-work-attempt",state.requestId)' in source
    assert "resultMetadata(result, live)" in source and "result.model" in source
    assert 'state.run.mode || "demo"' in source


def test_recover_lost_response_by_request_id_never_repeats_compare():
    demo, live = FakeService(), FakeLiveService()
    status, _, body = request(demo, path="/api/run?mode=live&request_id=browser-one",
                              allow_live=True, live_service=live)
    assert status == 200 and json.loads(body)["run"]["request_id"] == "browser-one"
    assert live.calls == [("get_by_request", "browser-one")] and demo.calls == []
    assert request(demo, path="/?mode=live&request_id=browser-one",
                   allow_live=True, live_service=live)[0] == 200
    assert len(live.calls) == 1


@pytest.mark.parametrize("query", ["request_id=../.env", "request_id=%2e%2e%2f.env", "request_id=",
                                  "request_id=א", "request_id=" + "x" * 101,
                                  "request_id=one&request_id=two", "request_id=one&run_id=" + RUN_ID])
def test_request_recovery_rejects_paths_ambiguous_or_invalid_ids(query):
    live = FakeLiveService()
    assert request(FakeService(), path="/api/run?mode=live&" + query,
                   allow_live=True, live_service=live)[0] == 400
    assert live.calls == []


def test_uncertain_recovery_returns_no_retry_without_exposing_exception():
    from comparison_flow import LiveOutcomeUncertain
    live = FakeLiveService(LiveOutcomeUncertain("SECRET"))
    status, _, body = request(FakeService(), path="/api/run?mode=live&request_id=one",
                              allow_live=True, live_service=live)
    data = json.loads(body)
    assert status == 409 and data["retry_allowed"] is False
    assert data["code"] == "live_outcome_uncertain" and "SECRET" not in data["error"]
    assert live.calls == [("get_by_request", "one")]


def test_preflight_estimate_and_reported_cost_have_separate_ui_locations():
    source = (local_ui.ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = (local_ui.ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="budget-summary"' in html and 'id="recover-live"' in html
    assert "budget.estimated_max_total" in source and "budget.approved_max_total" in source
    assert "budget.provider_estimates" in source and "result.paid_cost_estimate" in source
    assert 'openSaved(state.liveRequestId,true,"live")' in source


def test_startup_writes_hebrew_to_redirected_windows_output_without_calls(monkeypatch):
    from contextlib import nullcontext
    import sys
    output = io.BytesIO()
    redirected = io.TextIOWrapper(output, encoding="cp1252")
    constructed = []
    server = SimpleNamespace(serve_forever=lambda: None)
    monkeypatch.setattr(sys, "stdout", redirected)
    monkeypatch.setattr(sys, "argv", ["local_ui.py"])
    monkeypatch.setattr(local_ui, "make_server", lambda port, **kwargs: constructed.append((port, kwargs)) or nullcontext(server))
    local_ui.main()
    redirected.flush()
    assert redirected.encoding == "utf-8"
    assert "ברירת המחדל הדגמה" in output.getvalue().decode("utf-8")
    assert constructed == [(8765, {"allow_live": False, "allow_work": False})]


class FakeWorkService(FakeLiveService):
    def preview(self, prop):
        self.calls.append(("preview", prop))
        if self.error:
            raise self.error
        return {"property": prop, "budget_estimate": {"estimated_max_total": "0.09", "approved_max_total": "0.50", "currency": "USD"}}

    def compare(self, prop, request_id, *, data_consent=False):
        assert data_consent is True
        result = super().compare(prop, request_id)
        result["run"]["mode"] = "work"
        return result

    def get_run(self, run_id):
        result = super().get_run(run_id)
        result["run"]["mode"] = "work"
        return result

    def get_by_request(self, request_id):
        result = super().get_by_request(request_id)
        result["run"]["mode"] = "work"
        return result

    def list_runs(self):
        self.calls.append(("list_runs",))
        if self.error:
            raise self.error
        return {"runs": [{"run_id": RUN_ID, "request_id": "work-one", "date": "2026-10-24T10:00:00",
                           "status": "completed", "graded": False, "choice": None, "total_cost": None}]}


WORK = {**COMPARE, "mode": "work", "work_consent": True}


def test_work_schema_discloses_per_comparison_cap_and_google_privacy_without_starting_work():
    work = FakeWorkService()
    status, _, body = request(FakeService(), path="/api/schema", allow_work=True, work_service=work)
    data = json.loads(body)
    assert status == 200 and data["mode"] == "demo"
    metadata = data["modes"]["work"]
    assert metadata["available"] is True and metadata["max_total_usd"] == "0.50"
    assert metadata["limit_scope"] == "per_comparison" and metadata["sample_only"] is False
    assert metadata["google_billing_tier"] == "unknown"
    assert "מספר הבניין" in metadata["privacy_notice"] and "שיפור מוצריה" in metadata["privacy_notice"]
    assert work.calls == []


@pytest.mark.parametrize("allow,present", [(False, False), (False, True), (True, False)])
def test_work_post_preview_and_history_all_require_server_gate(allow, present):
    work = FakeWorkService()
    kwargs = {"allow_work": allow, "work_service": work if present else None}
    assert request(FakeService(), "POST", "/api/compare", WORK, **kwargs)[0] == 403
    assert request(FakeService(), "POST", "/api/preview", {"mode":"work","property":{}}, **kwargs)[0] == 403
    assert request(FakeService(), path="/api/history?mode=work", **kwargs)[0] == 403
    assert work.calls == []


@pytest.mark.parametrize("confirmation", [None, False, 1, "true", [], {}])
def test_work_requires_literal_consent_before_any_provider_service_call(confirmation):
    work = FakeWorkService()
    assert request(FakeService(), "POST", "/api/compare", {**WORK, "work_consent": confirmation},
                   allow_work=True, work_service=work)[0] == 403
    assert work.calls == []


def test_work_compare_preview_grade_and_read_routes_dispatch_only_to_work():
    demo, legacy, work = FakeService(), FakeLiveService(), FakeWorkService()
    kwargs = {"allow_work":True,"work_service":work,"live_service":legacy}
    assert request(demo, "POST", "/api/preview", {"mode":"work","property":COMPARE["property"]}, **kwargs)[0] == 200
    assert work.calls == [("preview",COMPARE["property"])]
    assert request(demo, "POST", "/api/compare", WORK, **kwargs)[0] == 200
    assert work.calls[-1] == ("compare",COMPARE["property"],COMPARE["request_id"])
    assert request(demo, "POST", "/api/grade", {**GRADE,"mode":"work"}, **kwargs)[0] == 200
    assert work.calls[-1][0] == "grade"
    assert request(demo, path="/api/run?mode=work&run_id="+RUN_ID, **kwargs)[0] == 200
    assert work.calls[-1] == ("get_run",RUN_ID)
    assert request(demo, path="/api/run?mode=work&request_id=work-one", **kwargs)[0] == 200
    assert work.calls[-1] == ("get_by_request","work-one")
    assert request(demo, path="/api/history?mode=work", **kwargs)[0] == 200
    assert work.calls[-1] == ("list_runs",)
    assert demo.calls == legacy.calls == []


@pytest.mark.parametrize("payload", [
    {"mode":"demo","property":{}}, {"mode":"live","property":{}},
    {"mode":"work"}, {"mode":"work","property":[]},
    {"mode":"work","property":{},"work_consent":True},
])
def test_preview_rejects_extra_fields_and_other_modes_without_generation(payload):
    work = FakeWorkService()
    assert request(FakeService(),"POST","/api/preview",payload,allow_work=True,work_service=work)[0] in (400,403)
    assert work.calls == []


def test_preview_validation_errors_are_422_without_comparison():
    work = FakeWorkService(InputValidationError(["מחיר"],["סתירה"]))
    status, _, body = request(FakeService(),"POST","/api/preview",{"mode":"work","property":{}},
                              allow_work=True,work_service=work)
    assert status == 422 and json.loads(body)["completion_requests"] == ["מחיר"]
    assert work.calls == [("preview",{})]


@pytest.mark.parametrize("query", ["", "?mode=demo", "?mode=live", "?mode=work&mode=work",
                                  "?mode=work&path=.env", "?mode=work&directory=runs"])
def test_history_does_not_accept_directories_other_modes_or_ambiguous_queries(query):
    work = FakeWorkService()
    assert request(FakeService(),path="/api/history"+query,allow_work=True,work_service=work)[0] == 400
    assert work.calls == []


@pytest.mark.parametrize("path", ["/runs/work/log.csv", "/runs/work/"+RUN_ID+".json",
                                "/api/run?mode=work&run_id=../.env",
                                "/api/run?mode=work&request_id=../private",
                                "/api/run?mode=work&run_id="+RUN_ID+"&mode=live"])
def test_work_storage_is_never_directly_exposed_and_recovery_cannot_escape(path):
    work = FakeWorkService()
    assert request(FakeService(),path=path,allow_work=True,work_service=work)[0] in (400,404)
    assert work.calls == []


def test_work_flag_permits_legacy_saved_read_without_enabling_paid_legacy_compare():
    live, work = FakeLiveService(), FakeWorkService()
    kwargs = {"allow_work":True,"work_service":work,"live_service":live}
    assert request(FakeService(),path="/api/run?mode=live&run_id="+RUN_ID,**kwargs)[0] == 200
    assert request(FakeService(),"POST","/api/compare",LIVE,**kwargs)[0] == 403
    assert live.calls == [("get_run",RUN_ID)] and work.calls == []


def test_work_factory_builds_fixed_cap_and_read_only_legacy_without_calls(monkeypatch):
    import comparison_flow
    constructed = []
    monkeypatch.setattr(comparison_flow,"OperationalComparisonService",lambda **kwargs: constructed.append(("work",kwargs)) or FakeWorkService())
    monkeypatch.setattr(comparison_flow,"LiveComparisonService",lambda **kwargs: constructed.append(("legacy",kwargs)) or FakeLiveService())
    monkeypatch.setattr(local_ui,"HTTPServer",lambda address,handler: address)
    assert local_ui.make_server(8765,FakeService(),allow_work=True) == ("127.0.0.1",8765)
    assert constructed == [("legacy",{"enabled":False,"max_cost_usd":"0.50","max_total_runs":1}),
                           ("work",{"enabled":True,"max_cost_usd":"0.50"})]


def test_work_html_has_open_required_groups_review_consent_and_original_input_snapshot():
    html = (local_ui.ROOT/"web"/"index.html").read_text(encoding="utf-8")
    source = (local_ui.ROOT/"web"/"app.js").read_text(encoding="utf-8")
    assert 'id="feature-fields"' in html and 'id="impression-fields"' in html
    assert 'id="work-consent" type="checkbox"' in html
    assert 'id="review-view"' in html and 'id="snapshot-facts"' in html
    assert "מספר הבניין הפנימי" in html and "שיפור מוצריה" in html
    assert 'api("/api/preview"' in source and "payload.work_consent = true" in source
    assert 'populateProperty(run.property)' in source and 'state.source="empty"' in source
    assert 'replaceAll(",", "")' in source
