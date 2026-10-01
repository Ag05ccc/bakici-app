"""Report receiver tests run with only the Python standard library."""

import concurrent.futures
import http.client
import json
import socket
import subprocess
import sys
import threading
import unittest

from registry import Registry
from web_app import ScannerHTTPServer


def configuration():
    return {"nodes": [
        {"id": f"pi-{number:02}", "label": f"Pi {number}", "floor": 1,
         "location": "Test room", "x": number * 20, "y": 50,
         "api_key": f"test-only-key-number-{number:02}"}
        for number in (1, 2)
    ]}


def report(node="pi-01", radio="bluetooth", observations=None):
    return {"node_id": node, "kind": "scan", "radio": radio,
            "status": "success", "observations": observations or []}


class ReportHTTPTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry(configuration())
        self.server = ScannerHTTPServer(("127.0.0.1", 0), registry=self.registry)
        self.server.request_timeout = 0.2
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def post(self, data, key="test-only-key-number-01"):
        return self.request("POST", "/api/reports", json.dumps(data), {
            "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        })

    def snapshot(self):
        status, body = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_report_sequence_empty_error_and_private_fields(self):
        self.assertEqual(self.snapshot()["nodes"][0]["state"], "waiting")
        self.assertEqual(self.post({"node_id": "pi-01", "kind": "heartbeat", "enabled_radios": ["bluetooth", "wifi"]})[0], 200)
        bt = [{"path": "/bt/1", "properties": {"Address": "AA:BB:CC:DD:EE:01", "RSSI": -50}},
              {"path": "/bt/2", "properties": {"Address": "AA:BB:CC:DD:EE:02", "RSSI": -60}}]
        wifi = [{"path": "/ap/1", "properties": {"BSSID": "AA:BB:CC:DD:EE:03", "SSID": "Fixture", "Strength": 73}}]
        self.assertEqual(self.post(report(observations=bt))[0], 200)
        self.assertEqual(self.post(report(radio="wifi", observations=wifi))[0], 200)
        state = self.snapshot()["nodes"][0]
        self.assertEqual(state["ip"], "127.0.0.1")
        self.assertEqual(state["radios"]["bluetooth"]["count"], 2)
        self.assertEqual(state["radios"]["wifi"]["count"], 1)
        self.assertEqual(self.post(report())[0], 200)
        self.assertEqual(self.post({"node_id": "pi-01", "kind": "scan", "radio": "wifi", "status": "error", "error": "Scan timed out"})[0], 200)
        snapshot = self.snapshot()
        radios = snapshot["nodes"][0]["radios"]
        self.assertEqual(radios["bluetooth"]["count"], 0)
        self.assertIsNone(radios["wifi"]["count"])
        self.assertEqual(radios["wifi"]["observations"], wifi)
        self.assertEqual(radios["wifi"]["state"], "error")
        self.assertEqual(snapshot["all_observations"]["wifi"], [])
        self.assertNotIn("api_key", json.dumps(snapshot))
        self.assertNotIn("test-only-key", json.dumps(snapshot))

    def test_invalid_reports_cannot_replace_good_state(self):
        records = [{"path": "/bt/1", "properties": {"Name": "Keep this"}}]
        self.assertEqual(self.post(report(observations=records))[0], 200)
        bad = [[], None, {}, report(node="unknown"), report(radio="other"),
               dict(report(), observations="not a list"), dict(report(), observations=[{}]),
               dict(report(), status="other"), dict(report(), observations=[{"path": "/x", "properties": {"RSSI": float("nan")}}])]
        for payload in bad:
            with self.subTest(payload=payload):
                self.assertGreaterEqual(self.post(payload)[0], 400)
        for key in ("", "wrong-key", "test-only-key-number-02"):
            self.assertIn(self.post(report(), key)[0], (401, 403))
        self.assertEqual(self.snapshot()["nodes"][0]["radios"]["bluetooth"]["observations"], records)

    def test_http_limits_content_type_and_no_config_access(self):
        self.assertEqual(self.request("POST", "/api/reports", "{}", {"Content-Type": "text/plain"})[0], 415)
        headers = {"Content-Type": "application/json", "Authorization": "Bearer test-only-key-number-01"}
        for raw in (b"{", b"\xff", b"[]", b"[" * 1100 + b"]" * 1100):
            self.assertEqual(self.request("POST", "/api/reports", raw, headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/reports", b"", dict(headers, **{"Content-Length": str(1024 * 1024 + 1)}))[0], 413)
        for path in ("/config/server.local.json", "/../registry.py", "/api/status", "/static/", "/requirements.txt"):
            self.assertEqual(self.request("GET", path)[0], 404)
        for path in ("/api/scan", "/api/stop"):
            self.assertEqual(self.request("POST", path, "{}", headers)[0], 404)

    def test_slow_sender_does_not_block_good_sender(self):
        sock = socket.create_connection(self.server.server_address, timeout=2)
        self.addCleanup(sock.close)
        sock.sendall(b"POST /api/reports HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
        self.assertEqual(self.post(report())[0], 200)
        self.assertEqual(self.snapshot()["nodes"][0]["state"], "online")
        response = http.client.HTTPResponse(sock)
        response.begin()
        self.assertEqual(response.status, 408)
        response.read()
        response.close()

    def test_parallel_nodes_and_polls_are_independent(self):
        def send(number):
            for _ in range(5):
                records = [{"path": f"/node/{number}", "properties": {"Name": f"Sensor {number}"}}]
                self.assertEqual(self.post(report(f"pi-{number:02}", observations=records), f"test-only-key-number-{number:02}")[0], 200)
                self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(send, (1, 2)))
        for number, node in enumerate(self.snapshot()["nodes"], 1):
            self.assertEqual(node["radios"]["bluetooth"]["observations"][0]["properties"]["Name"], f"Sensor {number}")

    def test_import_and_server_construction_without_site_packages(self):
        script = "from web_app import ScannerHTTPServer; from registry import Registry; import sys; "
        script += f"server = ScannerHTTPServer(('127.0.0.1', 0), registry=Registry({configuration()!r})); "
        script += "assert 'scan_bluetooth' not in sys.modules; assert 'dbus_fast' not in sys.modules; server.server_close()"
        result = subprocess.run([sys.executable, "-S", "-c", script], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
