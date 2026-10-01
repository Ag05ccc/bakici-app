import asyncio
import http.client
import json
import threading
import time
import unittest

from scan_bluetooth import ScanError
from web_app import ScanService, ScannerHTTPServer, scan_duration


RECORD = {
    "path": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
    "properties": {"Address": "AA:BB:CC:DD:EE:FF", "Name": "Test sensor", "RSSI": -42},
}


def eventually(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for the scanner worker")


async def fake_scan(duration, stop_event, *, on_update, on_status):
    on_status("Scanning test devices")
    on_update(RECORD)
    await stop_event.wait()
    return [RECORD]


class ServiceTests(unittest.TestCase):
    def test_streaming_stop_and_restart(self):
        service = ScanService(fake_scan)
        self.addCleanup(service.close)
        self.assertTrue(service.start(15))
        eventually(lambda: service.snapshot()["devices"])
        self.assertTrue(service.snapshot()["scanning"])
        self.assertEqual(service.snapshot()["devices"], [RECORD])
        self.assertFalse(service.start(15))
        service.stop()
        eventually(lambda: not service.snapshot()["scanning"])
        self.assertEqual(service.snapshot()["message"], "Scan stopped.")
        self.assertEqual(service.snapshot()["devices"], [RECORD])
        self.assertTrue(service.start(5))
        service.close()
        self.assertFalse(service.snapshot()["scanning"])
        self.assertFalse(service.start(5))

    def test_scan_error_preserves_partial_results_and_can_retry(self):
        async def failing_scan(duration, stop_event, *, on_update, on_status):
            on_update(RECORD)
            raise ScanError("Bluetooth was turned off.")

        service = ScanService(failing_scan)
        self.addCleanup(service.close)
        service.start(15)
        eventually(lambda: not service.snapshot()["scanning"])
        state = service.snapshot()
        self.assertEqual(state["error"], "Bluetooth was turned off.")
        self.assertEqual(state["devices"], [RECORD])
        self.assertTrue(service.start(15))

    def test_immediate_stop_is_honored_before_the_scan_starts(self):
        entered, release = threading.Event(), threading.Event()

        class DelayedService(ScanService):
            async def _scan_once(self, duration):
                entered.set()
                await asyncio.to_thread(release.wait, 2)
                return await super()._scan_once(duration)

        async def scan_after_stop(duration, stop_event, **kwargs):
            self.assertTrue(stop_event.is_set())
            return []

        service = DelayedService(scan_after_stop)
        self.addCleanup(service.close)
        service.start(15)
        self.assertTrue(entered.wait(1))
        service.stop()
        release.set()
        eventually(lambda: not service.snapshot()["scanning"])
        self.assertIsNone(service.snapshot()["error"])

    def test_successful_empty_scan_finishes(self):
        async def empty_scan(*args, **kwargs):
            return []

        service = ScanService(empty_scan)
        self.addCleanup(service.close)
        service.start(1)
        eventually(lambda: not service.snapshot()["scanning"])
        self.assertEqual(service.snapshot()["devices"], [])
        self.assertEqual(service.snapshot()["message"], "Scan complete.")

    def test_duration_validation(self):
        for value in [True, False, None, "15", 0, -1, 301, float("nan"), float("inf"), 10**1000]:
            with self.subTest(value=repr(value)[:30]), self.assertRaises(ValueError):
                scan_duration(value)
        self.assertEqual(scan_duration(15), 15)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = ScanService(fake_scan)
        self.server = ScannerHTTPServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.service.close()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, path, data):
        return self.request("POST", path, json.dumps(data), {"Content-Type": "application/json"})

    def test_assets_status_and_no_filesystem_browsing(self):
        for path, content_type in [("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")]:
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith(content_type))
                self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
                self.assertGreater(len(body), 0)
        status, headers, body = self.request("GET", "/api/status")
        self.assertFalse(json.loads(body)["scanning"])
        self.assertEqual(json.loads(body)["devices"], [])
        for path in ["/../scan_bluetooth.py", "/%2e%2e/scan_bluetooth.py", "/requirements.txt", "/static/"]:
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_scan_conflict_streaming_and_stop(self):
        self.assertEqual(self.post("/api/scan", {"timeout": 15})[0], 202)
        eventually(lambda: self.service.snapshot()["devices"])
        self.assertEqual(self.post("/api/scan", {"timeout": 15})[0], 409)
        status, _, body = self.request("GET", "/api/status")
        self.assertEqual(json.loads(body)["devices"], [RECORD])
        self.assertEqual(self.post("/api/stop", {})[0], 200)
        eventually(lambda: not self.service.snapshot()["scanning"])
        self.assertEqual(self.service.snapshot()["devices"], [RECORD])

    def test_invalid_requests_do_not_start_discovery(self):
        for data in [{"timeout": 0}, {"timeout": "15"}, {"timeout": True}, {"timeout": float("inf")}, []]:
            with self.subTest(data=data):
                self.assertEqual(self.post("/api/scan", data)[0], 400)
        self.assertEqual(self.request("POST", "/api/scan", "{", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/scan", "{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/scan", " " * 4097, {"Content-Type": "application/json"})[0], 400)
        self.assertFalse(self.service.snapshot()["scanning"])

    def test_cross_origin_scan_control_is_rejected(self):
        headers = {"Content-Type": "application/json", "Origin": "http://another-site.example"}
        self.assertEqual(self.request("POST", "/api/scan", "{}", headers)[0], 403)
        headers = {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}
        self.assertEqual(self.request("POST", "/api/stop", "{}", headers)[0], 403)
        self.assertFalse(self.service.snapshot()["scanning"])

    def test_same_origin_browser_requests_work(self):
        host, port = self.server.server_address
        headers = {"Content-Type": "application/json", "Origin": f"http://{host}:{port}"}
        self.assertEqual(self.request("POST", "/api/scan", "{}", headers)[0], 202)


if __name__ == "__main__":
    unittest.main()
