"""Loopback comparison screen. Demo default; live requires explicit server and browser opt-in."""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
MAX_BODY = 256 * 1024
LIVE_CAP_USD = "0.50"
RUN_ID_PATTERN = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")


def reject_constant(_value):
    raise ValueError("non-finite JSON number")

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


def create_handler(service, *, allow_live=False, live_service=None, allow_work=False, work_service=None):
    """Bind injected services. Live remains disabled unless both gates are present."""
    live_available = allow_live is True and live_service is not None
    work_available = allow_work is True and work_service is not None
    legacy_read_available = (allow_live is True or allow_work is True) and live_service is not None

    def saved_query(path):
        parsed = urlsplit(path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        reference_keys = set(query) - {"mode"}
        if query.get("mode") not in (["live"], ["work"]) or reference_keys not in ({"run_id"}, {"request_id"}):
            raise ValueError("invalid saved-run reference")
        key = next(iter(reference_keys))
        if len(query[key]) != 1:
            raise ValueError("invalid saved-run reference")
        value = query[key][0]
        valid = RUN_ID_PATTERN.fullmatch(value) if key == "run_id" else re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value)
        if not valid:
            raise ValueError("invalid saved-run reference")
        return query["mode"][0], key, value
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
                    self._send(200, {"template": template, "sample": sample, "mode": "demo",
                                     "modes": {"demo": {"available": True, "paid": False},
                                               "live": {"available": live_available, "paid": True,
                                                        "max_total_usd": LIVE_CAP_USD, "max_total_runs": 1,
                                                        "read_available": legacy_read_available,
                                                        "max_calls_per_provider": 1, "sample_only": True},
                                               "work": {"available": work_available, "paid": True,
                                                        "max_total_usd": LIVE_CAP_USD, "limit_scope": "per_comparison",
                                                        "max_calls_per_provider": 1, "sample_only": False,
                                                        "google_billing_tier": "unknown",
                                                        "privacy_notice": "כל נתוני הנכס וההתרשמות, כולל מספר הבניין הפנימי, נשלחים ל-Google ול-Anthropic. מסלול החיוב של Google לא אומת; במסלול החינמי Google עשויה להשתמש בתוכן לשיפור מוצריה."}}})
                elif urlsplit(self.path).path == "/api/history":
                    if not work_available:
                        self._error(403, "מצב עבודה אינו פתוח בשרת המקומי.")
                        return
                    if parse_qs(urlsplit(self.path).query, keep_blank_values=True) != {"mode": ["work"]}:
                        self._error(400, "נדרשת בקשה תקינה להרצות העבודה השמורות.")
                        return
                    self._send(200, work_service.list_runs())
                elif urlsplit(self.path).path == "/api/run":
                    try:
                        mode, reference_key, reference = saved_query(self.path)
                    except ValueError:
                        self._error(400, "נדרש מזהה תקין של הרצה שמורה.")
                        return
                    available = work_available if mode == "work" else legacy_read_available
                    if not available:
                        self._error(403, "מצב ההרצה אינו פתוח בשרת המקומי.")
                        return
                    selected = work_service if mode == "work" else live_service
                    result = selected.get_run(reference) if reference_key == "run_id" else selected.get_by_request(reference)
                    self._send(200, result)
                elif self.path.startswith("/?"):
                    try:
                        saved_query(self.path)
                    except ValueError:
                        self._error(404, "העמוד המבוקש לא נמצא.")
                        return
                    self._send(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif self.path in ASSETS:
                    filename, mime = ASSETS[self.path]
                    self._send(200, (ROOT / "web" / filename).read_bytes(), mime)
                else:
                    self._error(404, "העמוד המבוקש לא נמצא.")
            except FileNotFoundError:
                self._error(404, "ההרצה או העמוד המבוקשים לא נמצאו.")
            except ValueError as exc:
                if type(exc).__name__ == "LiveOutcomeUncertain":
                    self._error(409, "הניסיון נשמר אך תוצאתו אינה ודאית. לא תבוצע קריאה נוספת לספקים.",
                                code="live_outcome_uncertain", retry_allowed=False)
                else:
                    self._error(400, "לא ניתן לפתוח את ההרצה המבוקשת.")
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
            if self.path not in {"/api/compare", "/api/grade", "/api/preview"}:
                self._error(404, "הפעולה המבוקשת לא נמצאה.")
                return
            try:
                data = self._body()
            except (ValueError, UnicodeError, OverflowError, RecursionError):
                self._error(400, "הבקשה אינה תקינה. בדקו את הקלט ונסו שוב.")
                return
            if data is None:
                return
            mode = data.get("mode", "demo")
            if mode not in ("demo", "live", "work"):
                self._error(400, "מצב ההפעלה אינו תקין.")
                return
            if mode == "live" and not live_available:
                self._error(403, "מצב חי אינו פתוח בשרת המקומי.", retry_allowed=False)
                return
            if mode == "work" and not work_available:
                self._error(403, "מצב עבודה אינו פתוח בשרת המקומי.", retry_allowed=False)
                return
            selected_service = work_service if mode == "work" else live_service if mode == "live" else service
            if self.path == "/api/preview":
                if mode != "work" or set(data) != {"mode", "property"} or not isinstance(data.get("property"), dict):
                    self._error(400, "נדרשת בקשה תקינה לבדיקת נתוני העבודה.")
                    return
            elif self.path == "/api/compare":
                if mode == "work" and data.get("work_consent") is not True:
                    self._error(403, "יש לאשר שליחת נתוני הנכס וההתרשמות לשני הספקים, עד $0.50 להשוואה.",
                                retry_allowed=False)
                    return
                if mode == "live" and data.get("live_confirmed") is not True:
                    self._error(403, "יש לאשר במפורש קריאה אחת לכל ספק, בדוגמה הבדיונית בלבד, עד $0.50 בסך הכול.",
                                retry_allowed=False)
                    return
                if (set(data) - {"property", "request_id", "mode", "live_confirmed", "work_consent"}
                        or not isinstance(data.get("property"), dict)
                        or not isinstance(data.get("request_id"), str) or not data["request_id"]
                        or len(data["request_id"]) > 100):
                    self._error(400, "נדרשים נתוני נכס ומזהה פעולה תקינים.")
                    return
            else:
                if (set(data) - {"run_id", "scores", "choice", "note", "mode"}
                        or not isinstance(data.get("run_id"), str) or not data["run_id"]
                        or len(data["run_id"]) > 100 or not isinstance(data.get("scores"), dict)
                        or not isinstance(data.get("choice"), str) or not isinstance(data.get("note", ""), str)):
                    self._error(400, "נתוני הדירוג אינם תקינים.")
                    return
            try:
                if self.path == "/api/preview":
                    result = selected_service.preview(data["property"])
                elif self.path == "/api/compare":
                    if mode == "work":
                        result = selected_service.compare(data["property"], data["request_id"], data_consent=True)
                    else:
                        result = selected_service.compare(data["property"], data["request_id"])
                else:
                    result = selected_service.grade(data["run_id"], data["scores"], data["choice"], data.get("note", ""))
                self._send(200, result)
            except FileNotFoundError:
                self._error(404, "ההרצה לא נמצאה. הפעילו השוואה ונסו שוב.")
            except (ValueError, FileExistsError) as exc:
                if hasattr(exc, "completion_requests") and hasattr(exc, "problems"):
                    self._error(422, "יש להשלים או לתקן את נתוני הנכס.",
                                completion_requests=exc.completion_requests, problems=exc.problems)
                else:
                    if mode in ("live", "work") and self.path == "/api/compare":
                        self._error(409, "הפעולה חסומה או שמצבה אינו ודאי. אין להפעיל שוב; שחזרו את תוצאת הניסיון השמור.",
                                    code="live_outcome_uncertain" if type(exc).__name__ == "LiveOutcomeUncertain" else "live_blocked",
                                    retry_allowed=False)
                    else:
                        self._error(409, "הפעולה מתנגשת בנתונים שנשמרו או שהדירוג אינו תקין. בדקו את הבחירה והציונים.")
            except Exception:
                if mode in ("live", "work") and self.path == "/api/compare":
                    self._error(500, "מצב ההרצה אינו ידוע. אין להפעיל שוב; שחזרו את תוצאת הניסיון השמור.",
                                retry_allowed=False)
                else:
                    self._error(500, "הפעולה לא הושלמה. נסו שוב עם אותם נתונים.")

        def do_HEAD(self):
            self._error(405, "שיטת הבקשה אינה נתמכת.")

        def do_OPTIONS(self):
            self._error(405, "שיטת הבקשה אינה נתמכת.")

    return LocalHandler


def make_server(port=8765, service=None, *, allow_live=False, live_service=None, allow_work=False, work_service=None):
    if service is None:
        from comparison_flow import DemoComparisonService
        service = DemoComparisonService()
    if (allow_live is True or allow_work is True) and live_service is None:
        from comparison_flow import LiveComparisonService
        live_service = LiveComparisonService(enabled=allow_live is True, max_cost_usd=LIVE_CAP_USD, max_total_runs=1)
    if allow_work is True and work_service is None:
        from comparison_flow import OperationalComparisonService
        work_service = OperationalComparisonService(enabled=True, max_cost_usd=LIVE_CAP_USD)
    return HTTPServer(("127.0.0.1", port), create_handler(service, allow_live=allow_live, live_service=live_service,
                                                       allow_work=allow_work, work_service=work_service))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="מסך השוואת טיוטות מקומי — הדגמה כברירת מחדל")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-live", action="store_true", help="אפשר אישור מפורש להרצה חיה יחידה, בדוגמה בלבד ועד $0.50")
    parser.add_argument("--allow-work", action="store_true", help="אפשר עבודה מקומית עם אישור מפורש ועד $0.50 לכל השוואה")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    with make_server(args.port, allow_live=args.allow_live, allow_work=args.allow_work) as server:
        print(f"מסך מקומי — ברירת המחדל הדגמה: http://127.0.0.1:{args.port}")
        if args.allow_live:
            print("מצב חי זמין רק לאחר אישור במסך; דוגמה בדיונית בלבד, הרצה יחידה ועד $0.50 בסך הכול.")
        if args.allow_work:
            print("מצב עבודה זמין לאחר אישור במסך: קריאה אחת לכל ספק ועד $0.50 לכל השוואה.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
