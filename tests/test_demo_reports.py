import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools import demo_reports


ROOT = Path(__file__).resolve().parents[1]


class DemoReportsTests(unittest.TestCase):
    def test_example_has_nine_nodes_on_three_floors(self):
        nodes = demo_reports.load_nodes(ROOT / "config/server.example.json")
        self.assertEqual([node["id"] for node in nodes], [f"pi-{index:02}" for index in range(1, 10)])
        self.assertEqual(len({node["api_key"] for node in nodes}), 9)
        for floor in (1, 2, 3):
            group = [node for node in nodes if node["floor"] == floor]
            self.assertEqual([node["x"] for node in group], [20, 50, 80])
            self.assertTrue(all(node["y"] == 50 for node in group))

    def test_cli_posts_all_steps_without_printing_key(self):
        received = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, dict(self.headers), json.loads(body)))
                self.send_response(202)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "server.local.json"
                key = "test-only-private-key-not-to-echo"
                path.write_text(json.dumps({"nodes": [{"id": "test-pi", "api_key": key}]}))
                result = subprocess.run([
                    sys.executable, "-S", str(ROOT / "tools/demo_reports.py"),
                    "--server", f"http://127.0.0.1:{server.server_port}",
                    "--config", str(path), "--scenario", "one-node", "--auto",
                ], capture_output=True, text=True, timeout=10)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(key, result.stdout + result.stderr)
        self.assertIn("Step 5/5:", result.stdout)
        self.assertEqual(len(received), 5)
        self.assertEqual([item[2] for item in received],
                         [report for _, report in demo_reports.one_node_steps("test-pi")])
        for path, headers, _ in received:
            self.assertEqual(path, "/api/reports")
            self.assertEqual(headers["Authorization"], "Bearer " + key)
            self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(received[0][2]["enabled_radios"], ["bluetooth", "wifi"])
        self.assertEqual(len(received[1][2]["observations"]), 2)
        self.assertEqual(len(received[2][2]["observations"]), 1)
        self.assertEqual(received[3][2]["observations"], [])
        self.assertEqual(received[4][2]["status"], "error")

    def test_bad_config_does_not_echo_contents(self):
        key = "secret-example-do-not-echo"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"nodes": ' + key)
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                status = demo_reports.main(["--config", str(path), "--auto"])
        self.assertEqual(status, 1)
        self.assertNotIn(key, output.getvalue())

    def test_credentials_in_server_url_rejected(self):
        with self.assertRaises(ValueError):
            demo_reports.report_url("http://user:password@localhost:8001")

    def test_http_error_does_not_echo_response_body_or_key(self):
        key = "test-secret-do-not-echo"
        error = HTTPError("http://localhost/api/reports", 403, "Denied", {}, io.BytesIO(key.encode()))
        output = io.StringIO()
        with patch.object(demo_reports, "load_nodes", return_value=[{"id": "pi-test", "api_key": key}]), \
                patch.object(demo_reports, "send_report", side_effect=error), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = demo_reports.main(["--auto"])
        error.close()
        self.assertEqual(status, 1)
        self.assertIn("HTTP 403", output.getvalue())
        self.assertNotIn(key, output.getvalue())


if __name__ == "__main__":
    unittest.main()
