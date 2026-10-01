#!/usr/bin/env python3
"""A small browser interface for the Bluetooth scanner, using only stdlib HTTP."""

import argparse
import asyncio
import json
import logging
import math
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import sys
import threading
import time
from urllib.parse import urlsplit

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
BUILDING_STATIC_FILES = {
    "/": ("building.html", "text/html; charset=utf-8"),
    "/building.js": ("building.js", "text/javascript; charset=utf-8"),
    "/building.css": ("building.css", "text/css; charset=utf-8"),
}


def scan_duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Scan duration must be a number between 1 and 300 seconds.")
    if not 1 <= value <= 300 or not math.isfinite(value):
        raise ValueError("Scan duration must be between 1 and 300 seconds.")
    return float(value)


class ScanService:
    """Share one scan across browser clients; own its event loop in a worker."""

    def __init__(self, scan_function=None):
        # Server-only mode must run on a machine without radio dependencies.
        from scan_bluetooth import ScanError, scan, signal_sort_key
        self._scan = scan_function or scan
        self._scan_error = ScanError
        self._sort_key = signal_sort_key
        self._lock = threading.Lock()
        self._thread = None
        self._loop = None
        self._stop_event = None
        self._closing = False
        self._scanning = False
        self._stopping = False
        self._devices = {}
        self._error = None
        self._message = "Ready to scan."
        self._timeout = 15.0
        self._started = None
        self._finished = None

    def snapshot(self):
        with self._lock:
            elapsed = 0 if self._started is None else (self._finished or time.monotonic()) - self._started
            return {
                "scanning": self._scanning,
                "stopping": self._stopping,
                "message": self._message,
                "error": self._error,
                "timeout": self._timeout,
                "elapsed": round(elapsed, 1),
                "devices": sorted(self._devices.values(), key=self._sort_key),
            }

    def start(self, duration):
        duration = scan_duration(duration)
        with self._lock:
            if self._closing or self._scanning:
                return False
            self._scanning = True
            self._stopping = False
            self._devices = {}
            self._error = None
            self._message = "Starting discovery..."
            self._timeout = duration
            self._started = time.monotonic()
            self._finished = None
            self._thread = threading.Thread(target=self._work, args=(duration,), name="bluetooth-scan")
            try:
                self._thread.start()
            except RuntimeError:
                self._scanning = False
                self._error = "Could not start the scanner worker. Please retry."
                self._message = self._error
                raise
            return True

    def stop(self):
        with self._lock:
            if not self._scanning:
                return
            self._stopping = True
            self._message = "Stopping discovery..."
            loop, stop_event = self._loop, self._stop_event
        if loop is not None:
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                pass  # The worker finished between unlocking and signaling.

    def _on_update(self, record):
        with self._lock:
            self._devices[record["path"]] = record

    def _on_status(self, message):
        with self._lock:
            if not self._stopping:
                self._message = message

    async def _scan_once(self, duration):
        stop_event = asyncio.Event()
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._stop_event = stop_event
            if self._stopping:
                stop_event.set()
        return await self._scan(duration, stop_event, on_update=self._on_update, on_status=self._on_status)

    def _work(self, duration):
        error = None
        records = None
        try:
            records = asyncio.run(self._scan_once(duration))
        except self._scan_error as exc:
            error = str(exc)
        except Exception:
            logging.exception("Unexpected scanner failure")
            error = "The scanner encountered an unexpected error. Check the server terminal and retry."
        finally:
            with self._lock:
                if records is not None:
                    self._devices = {record["path"]: record for record in records}
                self._error = error
                self._message = error or ("Scan stopped." if self._stopping else "Scan complete.")
                self._finished = time.monotonic()
                self._loop = self._stop_event = None
                self._scanning = self._stopping = False

    def close(self):
        with self._lock:
            self._closing = True
            thread = self._thread
        self.stop()
        if thread is not None and thread.ident is not None:
            thread.join()  # scan() releases BlueZ before the server process exits.


class ScannerHTTPServer(ThreadingHTTPServer):
    request_timeout = 10

    def __init__(self, address, service=None, *, registry=None):
        self.service = service
        self.registry = registry
        super().__init__(address, ScannerHandler)


class ScannerHandler(BaseHTTPRequestHandler):
    server_version = "BluetoothScanner/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.request_timeout)

    def log_request(self, code="-", size="-"):
        # Polling should not fill the terminal or the Pi's logs.
        if isinstance(code, int) and code >= 400:
            super().log_request(code, size)

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        path = urlsplit(self.path).path
        files = STATIC_FILES
        if self.server.registry is not None:
            files = BUILDING_STATIC_FILES
            if path == "/api/dashboard":
                self._send(HTTPStatus.OK, self.server.registry.snapshot())
                return
        elif path == "/api/status":
            self._send(HTTPStatus.OK, self.server.service.snapshot())
            return
        if path in files:
            filename, content_type = files[path]
            try:
                content = (STATIC_DIR / filename).read_bytes()
            except OSError:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "A frontend file is missing. Copy the static folder with web_app.py."})
                return
            self._send(HTTPStatus.OK, content, content_type)
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if self.server.registry is not None:
            self._receive_report(path)
            return
        if path not in {"/api/scan", "/api/stop"}:
            self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        origin = self.headers.get("Origin")
        if (origin and origin != "http://" + self.headers.get("Host", "")) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send(HTTPStatus.FORBIDDEN, {"error": "Use the scanner page to control discovery."})
            return
        if self.headers.get_content_type() != "application/json":
            self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send an application/json request."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Send a JSON object smaller than 4 KB.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Send a JSON object.")
            if path == "/api/scan":
                if not self.server.service.start(data.get("timeout", 15)):
                    self._send(HTTPStatus.CONFLICT, {"error": "A scan is already running or the server is closing."})
                    return
            else:
                self.server.service.stop()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except TimeoutError:
            self._send(HTTPStatus.REQUEST_TIMEOUT, {"error": "The request body did not arrive in time."})
            return
        except RuntimeError:
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Could not start the scanner worker. Please retry."})
            return
        self._send(HTTPStatus.ACCEPTED if path == "/api/scan" else HTTPStatus.OK, self.server.service.snapshot())

    def _receive_report(self, path):
        from registry import ReportError
        if path != "/api/reports":
            self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        if self.headers.get_content_type() != "application/json":
            self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send an application/json request."})
            return
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise ValueError("Send one Content-Length header; transfer encoding is unsupported.")
            length = int(self.headers["Content-Length"])
            if length > 1024 * 1024:
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Report exceeds 1 MiB."})
                return
            if length <= 0:
                raise ValueError("Send a nonempty JSON object.")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request body.")
            def reject_constant(value):
                raise ValueError("JSON numbers must be finite.")
            data = json.loads(raw, parse_constant=reject_constant)
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            self.server.registry.accept(data, token, self.client_address[0])
        except ReportError as exc:
            self._send(exc.status, {"error": str(exc)})
            return
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON report or request length."})
            return
        except TimeoutError:
            self._send(HTTPStatus.REQUEST_TIMEOUT, {"error": "The request body did not arrive in time."})
            return
        self._send(HTTPStatus.OK, {"accepted": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "server"), default="local", help="local Bluetooth GUI or building report server")
    parser.add_argument("--config", help="server registry JSON file (required in server mode)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (use 0.0.0.0 to access from another computer)")
    parser.add_argument("--port", type=int, help="HTTP port (default: 8000 local, 8001 server)")
    args = parser.parse_args()
    if args.port is None:
        args.port = 8001 if args.mode == "server" else 8000
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    service = None
    try:
        if args.mode == "server":
            from registry import Registry, load_config
            if not args.config:
                parser.error("--config is required in server mode")
            server = ScannerHTTPServer((args.host, args.port), registry=Registry(load_config(args.config)))
        else:
            service = ScanService()
            server = ScannerHTTPServer((args.host, args.port), service)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Cannot start the web server: {exc}\n")
    display_host = "<Pi IP address>" if args.host == "0.0.0.0" else args.host
    print(f"Open http://{display_host}:{args.port} in your browser. Press Ctrl+C to stop.", flush=True)
    previous_term_handler = signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping the web server...", file=sys.stderr)
    finally:
        # Reject new scans before waiting for cleanup and closing HTTP sockets.
        if service is not None:
            service.close()
        server.server_close()
        signal.signal(signal.SIGTERM, previous_term_handler)
    return 0


if __name__ == "__main__":
    sys.exit(main())
