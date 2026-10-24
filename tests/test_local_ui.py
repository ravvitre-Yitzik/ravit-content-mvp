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


def request(service, method="GET", path="/", payload=None, *, body=None, headers=None, remove=()):
    handler_type = local_ui.create_handler(service)
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
    assert status == 200 and set(data) == {"template", "sample", "mode"}
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
