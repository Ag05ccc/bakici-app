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

    def test_building_fixtures_have_overlaps_and_distinct_address_types(self):
        fixtures = [demo_reports.building_reports(f"pi-{index:02}", index) for index in range(1, 10)]
        self.assertEqual([len(reports[1]["observations"]) for reports in fixtures], [2, 3, 1, 2, 3, 1, 2, 3, 1])
        self.assertEqual([len(reports[2]["observations"]) for reports in fixtures], [2, 1, 2, 1, 2, 1, 2, 1, 2])
        first = fixtures[0][1]["observations"][0]["properties"]
        last = fixtures[8][1]["observations"][0]["properties"]
        self.assertEqual(first["Address"], last["Address"])
        self.assertEqual((first["AddressType"], last["AddressType"]), ("public", "random"))
        self.assertNotIn("Address", fixtures[7][1]["observations"][2]["properties"])
        access_points = fixtures[0][2]["observations"]
        self.assertEqual(access_points[0]["properties"]["SSID"], access_points[1]["properties"]["SSID"])
        self.assertNotEqual(access_points[0]["properties"]["BSSID"], access_points[1]["properties"]["BSSID"])
        self.assertEqual(access_points[0]["properties"]["BSSID"],
                         fixtures[1][2]["observations"][0]["properties"]["BSSID"])

    def test_nine_node_cli_two_cycles_pause_error_empty_and_auth(self):
        received = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                report = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, self.headers["Authorization"], report))
                self.send_response(202)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            result = subprocess.run([
                sys.executable, "-S", str(ROOT / "tools/demo_reports.py"),
                "--server", f"http://127.0.0.1:{server.server_port}",
                "--config", str(ROOT / "config/server.example.json"),
                "--scenario", "nine-nodes", "--cycles", "2", "--interval", "0.001",
                "--pause-node", "pi-09", "--error-radio", "pi-08:wifi", "--empty-radio", "pi-07:bluetooth",
            ], capture_output=True, text=True, timeout=10)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(received), 49)
        self.assertIn("Cycle 1: sent 25 reports; 1 nodes paused; 0 upload errors.", result.stdout)
        self.assertIn("Cycle 2: sent 24 reports; 1 nodes paused; 0 upload errors.", result.stdout)
        nodes = demo_reports.load_nodes(ROOT / "config/server.example.json")
        keys = {node["id"]: node["api_key"] for node in nodes}
        for path, auth, report in received:
            self.assertEqual(path, "/api/reports")
            self.assertEqual(auth, "Bearer " + keys[report["node_id"]])
            self.assertNotEqual(report["node_id"], "pi-09")
            if report["node_id"] == "pi-07" and report.get("radio") == "bluetooth":
                self.assertEqual(report["observations"], [])
        for key in keys.values():
            self.assertNotIn(key, result.stdout + result.stderr)
        radio_errors = [report for _, _, report in received if report["node_id"] == "pi-08" and report.get("radio") == "wifi"]
        self.assertEqual([report["status"] for report in radio_errors], ["success", "error", "error"])
        self.assertEqual(len(radio_errors[0]["observations"]), 1)
        for node in nodes[:-1]:
            reports = [report for _, _, report in received if report["node_id"] == node["id"]]
            self.assertEqual(sum(report["kind"] == "heartbeat" for report in reports), 2)

    def test_building_continues_after_failed_upload(self):
        output = io.StringIO()
        nodes = [{"id": "pi-test", "api_key": "test-private-key-to-hide"}]
        with patch.object(demo_reports, "load_nodes", return_value=nodes), \
                patch.object(demo_reports, "send_report", side_effect=[OSError("unavailable"), 202, 202, 202, 202, 202]) as send, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = demo_reports.main(["--scenario", "nine-nodes", "--cycles", "2", "--interval", "0.001"])
        self.assertEqual(result, 1)
        self.assertEqual(send.call_count, 6)
        self.assertIn("Cycle 2: sent 3 reports", output.getvalue())
        self.assertNotIn(nodes[0]["api_key"], output.getvalue())

    def test_building_ctrl_c_exits_without_traceback(self):
        output = io.StringIO()
        with patch.object(demo_reports, "send_report", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = demo_reports.main(["--config", str(ROOT / "config/server.example.json"), "--scenario", "nine-nodes"])
        self.assertEqual(result, 130)
        self.assertIn("Demo stopped.", output.getvalue())

    def test_invalid_building_timing_rejected(self):
        for flag, value in [("--interval", "0"), ("--interval", "-1"), ("--interval", "nan"),
                            ("--interval", "inf"), ("--cycles", "0"), ("--cycles", "1.5")]:
            with self.subTest(flag=flag, value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    demo_reports.main(["--scenario", "nine-nodes", flag, value])
                self.assertEqual(raised.exception.code, 2)

    def test_unknown_or_conflicting_radio_controls_rejected(self):
        for options in (["--pause-node", "missing"], ["--error-radio", "pi-01:unknown"],
                        ["--empty-radio", "missing:wifi"],
                        ["--error-radio", "pi-01:wifi", "--empty-radio", "pi-01:wifi"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(demo_reports, "send_report") as send:
                result = demo_reports.main(["--config", str(ROOT / "config/server.example.json"),
                                            "--scenario", "nine-nodes", "--cycles", "1", *options])
                self.assertEqual(result, 1)
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
