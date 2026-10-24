"""Local, demonstration-only comparison screen. No provider calls or keys at startup."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAX_BODY = 256 * 1024


def reject_constant(_value):
    raise ValueError("non-finite JSON number")

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


def create_handler(service):
    """Bind an injected demo service; all filesystem routes are fixed allowlists."""
    class LocalHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def version_string(self):
            return "RavitLocal"

        def send_error(self, code, message=None, explain=None):
            self._error(code, "הבקשה אינה נתמכת או אינה תקינה.")

        def log_message(self, *_args):
            pass  # Request bodies, property data and exception details never enter access logs.

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; "
                             "base-uri 'none'; form-action 'none'")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _error(self, status, message, **details):
            self._send(status, {"error": message, **details})

        def _local_request(self, require_origin=False):
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if port == 80:
                hosts.update({"127.0.0.1", "localhost"})
            host_values = self.headers.get_all("Host", [])
            if len(host_values) != 1 or host_values[0] not in hosts:
                self._error(403, "כתובת הבקשה אינה מקומית ומאושרת.")
                return False
            origins = self.headers.get_all("Origin", [])
            allowed = {"http://" + host for host in hosts}
            if (require_origin and not origins) or len(origins) > 1 or (origins and origins[0] not in allowed):
                self._error(403, "אפשר לבצע פעולה רק מתוך המסך המקומי.")
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self._error(403, "אפשר לבצע פעולה רק מתוך המסך המקומי.")
                return False
            return True

        def do_GET(self):
            if not self._local_request():
                return
            try:
                if self.path == "/api/schema":
                    template = json.loads((ROOT / "data" / "property-template.json").read_text(encoding="utf-8"))
                    sample = json.loads((ROOT / "data" / "sample-property.json").read_text(encoding="utf-8"))
                    self._send(200, {"template": template, "sample": sample, "mode": "demo"})
                elif self.path in ASSETS:
                    filename, mime = ASSETS[self.path]
                    self._send(200, (ROOT / "web" / filename).read_bytes(), mime)
                else:
                    self._error(404, "העמוד המבוקש לא נמצא.")
            except Exception:
                self._error(500, "לא ניתן לטעון את המסך. נסו שוב.")

        def _body(self):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError()
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise ValueError()
            length = int(lengths[0])
            if length > MAX_BODY:
                self._error(413, "הקלט גדול מדי. יש לקצר את הטקסט ולנסות שוב.")
                return None
            content_types = self.headers.get_all("Content-Type", [])
            if len(content_types) != 1 or content_types[0].split(";")[0].strip().lower() != "application/json":
                self._error(415, "נדרש קלט בפורמט JSON.")
                return None
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            data = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
            if not isinstance(data, dict):
                raise ValueError()
            return data

        def do_POST(self):
            if not self._local_request(require_origin=True):
                return
            if self.path not in {"/api/compare", "/api/grade"}:
                self._error(404, "הפעולה המבוקשת לא נמצאה.")
                return
            try:
                data = self._body()
            except (ValueError, UnicodeError, OverflowError, RecursionError):
                self._error(400, "הבקשה אינה תקינה. בדקו את הקלט ונסו שוב.")
                return
            if data is None:
                return
            if self.path == "/api/compare":
                if (set(data) != {"property", "request_id"} or not isinstance(data["property"], dict)
                        or not isinstance(data["request_id"], str) or not data["request_id"]
                        or len(data["request_id"]) > 100):
                    self._error(400, "נדרשים נתוני נכס ומזהה פעולה תקינים.")
                    return
            else:
                if (set(data) - {"run_id", "scores", "choice", "note"}
                        or not isinstance(data.get("run_id"), str) or not data["run_id"]
                        or len(data["run_id"]) > 100 or not isinstance(data.get("scores"), dict)
                        or not isinstance(data.get("choice"), str) or not isinstance(data.get("note", ""), str)):
                    self._error(400, "נתוני הדירוג אינם תקינים.")
                    return
            try:
                if self.path == "/api/compare":
                    result = service.compare(data["property"], data["request_id"])
                else:
                    result = service.grade(data["run_id"], data["scores"], data["choice"], data.get("note", ""))
                self._send(200, result)
            except FileNotFoundError:
                self._error(404, "ההרצה לא נמצאה. הפעילו השוואה ונסו שוב.")
            except (ValueError, FileExistsError) as exc:
                if hasattr(exc, "completion_requests") and hasattr(exc, "problems"):
                    self._error(422, "יש להשלים או לתקן את נתוני הנכס.",
                                completion_requests=exc.completion_requests, problems=exc.problems)
                else:
                    self._error(409, "הפעולה מתנגשת בנתונים שנשמרו או שהדירוג אינו תקין. בדקו את הבחירה והציונים.")
            except Exception:
                self._error(500, "הפעולה לא הושלמה. נסו שוב עם אותם נתונים; לא נשלחה בקשה לספקים.")

        def do_HEAD(self):
            self._error(405, "שיטת הבקשה אינה נתמכת.")

        def do_OPTIONS(self):
            self._error(405, "שיטת הבקשה אינה נתמכת.")

    return LocalHandler


def make_server(port=8765, service=None):
    if service is None:
        from comparison_flow import DemoComparisonService
        service = DemoComparisonService()
    return HTTPServer(("127.0.0.1", port), create_handler(service))


def main():
    parser = argparse.ArgumentParser(description="מסך השוואת טיוטות מקומי — הדגמה בלבד")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    with make_server(args.port) as server:
        print(f"מצב הדגמה — ללא פנייה לספקים: http://127.0.0.1:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
