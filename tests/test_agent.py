"""Agent tests use synthetic scanners and standard-library HTTP, never radios."""

import asyncio
import copy
import http.client
import json
import subprocess
import sys
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from agent import Agent, ConfigError, ReportSender, load_config, parse_config, post_report
from registry import Registry
from web_app import ScannerHTTPServer


KEY = "test-only-agent-key-01"
RECORD = {"path": "/synthetic/1", "properties": {"Address": "AA:BB:CC:DD:EE:01", "RSSI": -42}}


def configuration(**updates):
    data = {"node_id": "pi-01", "api_key": KEY, "server_url": "http://127.0.0.1:8001"}
    data.update(updates)
    return parse_config(data)


async def eventually(predicate, timeout=1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.002)
    raise AssertionError("Agent condition did not complete")


class MemorySender:
    def __init__(self):
        self.reports = []
        self.closed = False

    def submit(self, lane, report):
        self.reports.append((lane, copy.deepcopy(report)))
        return True

    async def close(self):
        self.closed = True


class AgentConfigTests(unittest.TestCase):
    def test_defaults_example_and_enabled_combinations(self):
        config = load_config("config/agent.example.json")
        self.assertEqual(config.bluetooth_timeout, 15)
        self.assertEqual(config.bluetooth_pause, 5)
        self.assertEqual(config.wifi_interval, 60)
        self.assertEqual(config.heartbeat_interval, 20)
        self.assertNotIn(config.api_key, repr(config))
        for radios in ([], ["bluetooth"], ["wifi"], ["bluetooth", "wifi"]):
            self.assertEqual(configuration(enabled_radios=radios).enabled_radios, tuple(radios))
        config = configuration(bluetooth={"adapter": "hci2"}, wifi={"interface": "wlan1"})
        self.assertEqual((config.bluetooth_adapter, config.wifi_interface), ("hci2", "wlan1"))

    def test_bad_config_and_secret_free_errors(self):
        bad_values = [
            {"node_id": "../pi"}, {"node_id": True}, {"node_id": ""},
            {"api_key": "short"}, {"api_key": " " * 20}, {"api_key": "é" * 20},
            {"enabled_radios": "wifi"}, {"enabled_radios": ["wifi", "wifi"]},
            {"enabled_radios": [False]}, {"enabled_radios": ["gps"]},
            {"bluetooth": {"adapter": "wlan0"}}, {"wifi": {"interface": "bad interface"}},
            {"wifi": []}, {"wifi": {"unknown": 10}}, {"typo": 1},
        ]
        for update in bad_values:
            with self.subTest(update=update), self.assertRaises(ConfigError) as caught:
                configuration(**update)
            self.assertNotIn(KEY, str(caught.exception))
        for value in (None, [], "text"):
            with self.assertRaises(ConfigError):
                parse_config(value)
        with self.assertRaises(ConfigError):
            parse_config({})

    def test_invalid_timing_and_urls(self):
        for value in (0, -1, True, "15", None, float("inf"), float("nan"), 10 ** 1000):
            for section, setting in (("bluetooth", "timeout"), ("bluetooth", "pause"),
                                     ("wifi", "timeout"), ("wifi", "interval")):
                with self.subTest(value=value, section=section, setting=setting), self.assertRaises(ConfigError):
                    configuration(**{section: {setting: value}})
            with self.assertRaises(ConfigError):
                configuration(heartbeat_interval=value)
        for url in (None, "localhost:8001", "ftp://host", "http://", "http://host:0", "http://host:99999",
                    "http://host/path", "http://host?a=b", "http://host/#x", "http://user:pass@host", "http://host\n"):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                configuration(server_url=url)
        self.assertEqual(configuration(server_url="https://[::1]:8443/").server_url, "https://[::1]:8443")

    def test_import_without_site_packages_or_radio_imports(self):
        result = subprocess.run([sys.executable, "-S", "-c",
            "import sys, agent; assert 'dbus_fast' not in sys.modules; assert 'scan_bluetooth' not in sys.modules; assert 'scan_wifi' not in sys.modules"],
            capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_bluetooth_pause_and_wifi_interval_use_scan_start(self):
        for radio, scan_time, expected_delay in (("bluetooth", 15, 5), ("wifi", 15, 45), ("wifi", 75, 0)):
            with self.subTest(radio=radio, scan_time=scan_time):
                now = [100.0]
                calls, delays = [], []
                stop = asyncio.Event()
                sender = MemorySender()

                async def scanner(timeout, stop_event, **options):
                    calls.append((now[0], timeout, options))
                    now[0] += scan_time
                    return [RECORD]

                async def wait(delay, stop_event):
                    delays.append(delay)
                    now[0] += delay
                    if len(delays) == 3:
                        stop_event.set()
                    return stop_event.is_set()

                config = configuration(bluetooth={"adapter": "hci2"}, wifi={"interface": "wlan1"})
                agent = Agent(config, scanners={radio: scanner}, sender=sender, clock=lambda: now[0], wait=wait)
                await agent._radio_loop(radio, stop)
                self.assertEqual(delays, [expected_delay] * 3)
                self.assertEqual([call[0] for call in calls], [100, 100 + scan_time + expected_delay, 100 + 2 * (scan_time + expected_delay)])
                self.assertEqual(calls[0][2], {"adapter": "hci2"} if radio == "bluetooth" else {"interface": "wlan1"})
                self.assertEqual(len(sender.reports), 3)

    async def test_payload_success_empty_error_and_heartbeat(self):
        sender = MemorySender()
        stop = asyncio.Event()
        count = [0]

        async def scanner(*args, **kwargs):
            count[0] += 1
            if count[0] == 1:
                return [RECORD]
            if count[0] == 2:
                return []
            raise RuntimeError(KEY + "\n" + "x" * 1000)

        async def wait(delay, stop_event):
            if count[0] == 3:
                stop_event.set()
            return stop_event.is_set()

        agent = Agent(configuration(), scanners={"bluetooth": scanner}, sender=sender, wait=wait)
        await agent._radio_loop("bluetooth", stop)
        reports = [item[1] for item in sender.reports]
        self.assertEqual(reports[0], {"node_id": "pi-01", "kind": "scan", "radio": "bluetooth", "status": "success", "observations": [RECORD]})
        self.assertEqual(reports[1]["observations"], [])
        self.assertEqual(reports[2]["status"], "error")
        self.assertEqual(len(reports[2]["error"]), 500)
        self.assertNotIn(KEY, reports[2]["error"])
        self.assertNotIn("\n", reports[2]["error"])
        self.assertNotIn("observations", reports[2])
        stop.clear()
        await agent._heartbeat_loop(stop)
        self.assertEqual(sender.reports[-1], ("heartbeat", {"node_id": "pi-01", "kind": "heartbeat", "enabled_radios": ["bluetooth", "wifi"]}))

    async def test_one_hung_radio_does_not_stop_other_radio_or_heartbeat(self):
        for failed_radio in ("bluetooth", "wifi"):
            with self.subTest(failed_radio=failed_radio):
                stop, cleaned = asyncio.Event(), asyncio.Event()
                sender = MemorySender()

                async def hung(*args, **kwargs):
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaned.set()

                async def good(*args, **kwargs):
                    await asyncio.sleep(0.001)
                    return [RECORD]

                config = configuration(bluetooth={"timeout": 0.02, "pause": 0.01}, wifi={"timeout": 0.02, "interval": 0.01}, heartbeat_interval=0.01)
                good_radio = "wifi" if failed_radio == "bluetooth" else "bluetooth"
                agent = Agent(config, scanners={failed_radio: hung, good_radio: good}, sender=sender)
                with patch("agent.SETUP_GRACE", 0), patch("agent.CLEANUP_GRACE", 0.02):
                    task = asyncio.create_task(agent.run(stop))
                    await eventually(lambda: any(lane == failed_radio and report.get("status") == "error" for lane, report in sender.reports))
                    stop.set()
                    await asyncio.wait_for(task, 0.5)
                self.assertTrue(cleaned.is_set())
                self.assertTrue(any(lane == good_radio and report.get("status") == "success" for lane, report in sender.reports))
                self.assertGreaterEqual(sum(lane == "heartbeat" for lane, _ in sender.reports), 2)
                self.assertTrue(sender.closed)

    async def test_exception_and_missing_dependency_do_not_exit_process(self):
        sender, stop = MemorySender(), asyncio.Event()

        async def good(*args, **kwargs):
            return []

        config = configuration(bluetooth={"pause": 0.01}, wifi={"interval": 0.01}, heartbeat_interval=0.01)
        agent = Agent(config, scanners={"wifi": good}, sender=sender)
        with patch("agent.importlib.import_module", side_effect=SystemExit("missing dbus-fast")):
            task = asyncio.create_task(agent.run(stop))
            await eventually(lambda: any(lane == "bluetooth" and report.get("status") == "error" for lane, report in sender.reports))
            stop.set()
            await asyncio.wait_for(task, 0.5)
        self.assertTrue(any(lane == "wifi" for lane, _ in sender.reports))
        self.assertTrue(any(lane == "heartbeat" for lane, _ in sender.reports))

    async def test_disabled_radios_and_immediate_stop(self):
        sender, stop = MemorySender(), asyncio.Event()
        agent = Agent(configuration(enabled_radios=[]), sender=sender)
        with patch("agent.load_scanner", side_effect=AssertionError("disabled scanner imported")):
            task = asyncio.create_task(agent.run(stop))
            await eventually(lambda: sender.reports)
            stop.set()
            await task
        self.assertEqual(sender.reports, [("heartbeat", {"node_id": "pi-01", "kind": "heartbeat", "enabled_radios": []})])
        sender = MemorySender()
        await Agent(configuration(), sender=sender).run(stop)
        self.assertEqual(sender.reports, [])
        self.assertTrue(sender.closed)

    async def test_stop_waits_for_scanner_cleanup_and_does_not_publish_partial(self):
        stop, started, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sender = MemorySender()

        async def scanner(timeout, stop_event, **kwargs):
            try:
                started.set()
                await stop_event.wait()
                return [RECORD]
            finally:
                await asyncio.sleep(0.005)
                cleaned.set()

        agent = Agent(configuration(enabled_radios=["bluetooth"]), scanners={"bluetooth": scanner}, sender=sender)
        task = asyncio.create_task(agent.run(stop))
        await started.wait()
        stop.set()
        await asyncio.wait_for(task, 0.5)
        self.assertTrue(cleaned.is_set())
        self.assertFalse(any(lane == "bluetooth" for lane, _ in sender.reports))

    async def test_heartbeat_default_schedule(self):
        sender, stop, delays = MemorySender(), asyncio.Event(), []

        async def wait(delay, stop_event):
            delays.append(delay)
            if len(delays) == 3:
                stop_event.set()
            return stop_event.is_set()

        await Agent(configuration(), sender=sender, wait=wait)._heartbeat_loop(stop)
        self.assertEqual(delays, [20, 20, 20])
        self.assertEqual(len(sender.reports), 3)


class AgentUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_scanning_and_heartbeat_continue_while_upload_is_stalled(self):
        release = threading.Event()
        uploaded, scans = [], []
        stop = asyncio.Event()

        def uploader(config, report):
            if report["kind"] == "scan":
                release.wait(2)
            uploaded.append(report)

        async def scanner(*args, **kwargs):
            scans.append(len(scans) + 1)
            return [{"path": "/synthetic", "properties": {"serial": scans[-1]}}]

        config = configuration(enabled_radios=["bluetooth"], bluetooth={"pause": 0.005}, heartbeat_interval=0.005)
        sender = ReportSender(config, uploader=uploader)
        task = asyncio.create_task(Agent(config, scanners={"bluetooth": scanner}, sender=sender).run(stop))
        try:
            await eventually(lambda: len(scans) >= 4)
            self.assertGreaterEqual(sum(report["kind"] == "heartbeat" for report in uploaded), 2)
            self.assertEqual(sum(worker.is_alive() for worker in sender.workers.values()), 1)
            self.assertFalse(any(report["kind"] == "scan" for report in uploaded))
        finally:
            stop.set()
            release.set()
            await asyncio.wait_for(task, 0.5)

    async def test_slow_upload_bounded_lanes_no_replay_and_order(self):
        release, started = threading.Event(), threading.Event()
        uploaded = []

        def uploader(config, report):
            if report["serial"] == 1:
                started.set()
                release.wait(2)
            uploaded.append(report["serial"])

        sender = ReportSender(configuration(), uploader=uploader)
        try:
            self.assertTrue(sender.submit("bluetooth", {"serial": 1}))
            await eventually(started.is_set)
            for serial in range(2, 100):
                self.assertFalse(sender.submit("bluetooth", {"serial": serial}))
            self.assertTrue(sender.submit("wifi", {"serial": 100}))
            self.assertTrue(sender.submit("heartbeat", {"serial": 101}))
            await eventually(lambda: len(uploaded) == 2)
            self.assertLessEqual(len(sender.workers), 3)
            release.set()
            await eventually(lambda: not sender.workers["bluetooth"].is_alive())
            self.assertTrue(sender.submit("bluetooth", {"serial": 102}))
            await sender.close()
            self.assertEqual([serial for serial in uploaded if serial not in (100, 101)], [1, 102])
            self.assertFalse(sender.submit("bluetooth", {"serial": 103}))
        finally:
            release.set()
            await sender.close()

    async def test_failed_upload_is_discarded_and_new_scan_keeps_running(self):
        attempts, uploaded = [], []
        sender = None
        stop = asyncio.Event()
        serial = [0]

        def uploader(config, report):
            if report["kind"] == "heartbeat":
                return
            sequence = report["observations"][0]["properties"]["serial"]
            attempts.append(sequence)
            if sequence < 3:
                raise OSError("server unavailable")
            uploaded.append(sequence)

        async def scanner(*args, **kwargs):
            serial[0] += 1
            return [{"path": "/synthetic", "properties": {"serial": serial[0]}}]

        config = configuration(enabled_radios=["bluetooth"], bluetooth={"pause": 0.005}, heartbeat_interval=0.005)
        sender = ReportSender(config, uploader=uploader)
        agent = Agent(config, scanners={"bluetooth": scanner}, sender=sender)
        with self.assertLogs("radio-agent", level="WARNING"):
            task = asyncio.create_task(agent.run(stop))
            await eventually(lambda: len(uploaded) >= 2)
            stop.set()
            await task
        self.assertEqual(attempts[:4], [1, 2, 3, 4])
        self.assertEqual(uploaded[:2], [3, 4])

    async def test_shutdown_does_not_wait_forever_for_stuck_http(self):
        release = threading.Event()
        sender = ReportSender(configuration(), uploader=lambda *args: release.wait(2))
        sender.submit("heartbeat", {"kind": "heartbeat"})
        try:
            started = time.monotonic()
            await sender.close(timeout=0.01)
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertTrue(sender.workers["heartbeat"].daemon)
            self.assertFalse(sender.submit("wifi", {}))
        finally:
            release.set()
            await eventually(lambda: not sender.workers["heartbeat"].is_alive())


class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry({"nodes": [{"id": "pi-01", "label": "Pi 01", "floor": 1,
            "location": "Test room", "x": 20, "y": 50, "api_key": KEY}]})
        self.server = ScannerHTTPServer(("127.0.0.1", 0), registry=self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.config = configuration(server_url=f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def snapshot(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request("GET", "/api/dashboard")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())
        finally:
            connection.close()

    def test_payload_arrives_unchanged_and_server_rejects_wrong_key(self):
        post_report(self.config, {"node_id": "pi-01", "kind": "heartbeat", "enabled_radios": ["bluetooth"]})
        post_report(self.config, {"node_id": "pi-01", "kind": "scan", "radio": "bluetooth", "status": "success", "observations": [RECORD]})
        node = self.snapshot()["nodes"][0]
        self.assertEqual(node["ip"], "127.0.0.1")
        self.assertEqual(node["radios"]["bluetooth"]["observations"], [RECORD])
        self.assertEqual(node["radios"]["wifi"]["state"], "disabled")
        with self.assertRaises(OSError):
            post_report(replace(self.config, api_key="wrong-test-key-123"), {"node_id": "pi-01", "kind": "heartbeat", "enabled_radios": []})
        self.assertEqual(self.snapshot()["nodes"][0]["radios"]["bluetooth"]["count"], 1)

    def test_no_redirects_and_payload_size_and_number_limits(self):
        paths = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                paths.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/unexpected")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            with self.assertRaisesRegex(OSError, "HTTP 307"):
                post_report(replace(self.config, server_url=f"http://127.0.0.1:{server.server_port}"), {})
            self.assertEqual(paths, ["/api/reports"])
            with self.assertRaises(ValueError):
                post_report(self.config, {"large": "x" * (1024 * 1024)})
            with self.assertRaises(ValueError):
                post_report(self.config, {"invalid": float("nan")})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_delayed_connection_discards_report_before_transmitting(self):
        with patch("agent.http.client.HTTPConnection") as connection_type, patch("agent.time.monotonic", side_effect=[10, 14]):
            with self.assertRaises(TimeoutError):
                post_report(self.config, {"node_id": "pi-01", "kind": "heartbeat", "enabled_radios": []})
            connection = connection_type.return_value
            connection.connect.assert_called_once()
            connection.request.assert_not_called()
            connection.close.assert_called_once()

    def test_complete_agent_to_receiver_path(self):
        async def exercise():
            stop = asyncio.Event()

            async def bluetooth(*args, **kwargs):
                return [RECORD]

            async def wifi(*args, **kwargs):
                return [{"path": "/synthetic/ap", "properties": {"BSSID": "AA:BB:CC:DD:EE:02", "SSID": "Test AP", "Strength": 72}}]

            config = replace(self.config, bluetooth_pause=0.01, wifi_interval=0.01, heartbeat_interval=0.01)
            task = asyncio.create_task(Agent(config, scanners={"bluetooth": bluetooth, "wifi": wifi}).run(stop))
            try:
                await eventually(lambda: all(item["count"] == 1 for item in self.registry.snapshot()["nodes"][0]["radios"].values()))
            finally:
                stop.set()
                await asyncio.wait_for(task, 1)

        asyncio.run(exercise())
        node = self.snapshot()["nodes"][0]
        self.assertEqual(node["radios"]["bluetooth"]["observations"], [RECORD])
        self.assertEqual(node["radios"]["wifi"]["observations"][0]["properties"]["Strength"], 72)


if __name__ == "__main__":
    unittest.main()
