import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from tools import monitor_deployment as monitor


def dashboard(count=2):
    return {"nodes": [
        {"id": f"pi-{index:02}", "state": "online", "age": index, "heartbeat_age": index + 1,
         "api_key": "test-key-must-not-appear",
         "radios": {
             "bluetooth": {"state": "fresh", "age": index + 2, "error": "private error text",
                           "observations": [{"properties": {"Address": "02:12:34:56:78:90"}}]},
             "wifi": {"state": "fresh", "age": index + 3, "observations": [{"properties": {"SSID": "Private network"}}]},
         }} for index in range(1, count + 1)
    ], "all_observations": {"private": "must not appear"}}


class FakeClock:
    def __init__(self):
        self.now = 100
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise AssertionError("Negative sleep")
        self.sleeps.append(seconds)
        self.now += seconds


class MonitorTests(unittest.TestCase):
    def run_fake(self, payload=None, *, duration=5, interval=2, expected_nodes=2, pid=None, fetch=None,
                 resource_reader=None, node_ids=None, sleep=None):
        clock = FakeClock()
        records = []
        fetch = fetch or (lambda url, timeout: copy.deepcopy(payload if payload is not None else dashboard()))
        status = monitor.run_monitor("http://localhost/api/dashboard", duration, interval, expected_nodes, pid,
                                     fetch=fetch, clock=clock, sleep=sleep or clock.sleep,
                                     resource_reader=resource_reader, write=records.append, node_ids=node_ids)
        return status, records, clock

    def test_duration_samples_summary_and_no_private_data(self):
        status, records, clock = self.run_fake()
        self.assertEqual(status, 0)
        self.assertEqual([row["elapsed_seconds"] for row in records], [0, 2, 4, 5])
        self.assertEqual(clock.sleeps, [2, 2, 1])
        sample, summary = records[0], records[-1]
        self.assertEqual(sample["node_count"], 2)
        self.assertEqual(sample["online_nodes"], 2)
        self.assertEqual(sample["fresh_radios"], {"bluetooth": 2, "wifi": 2})
        self.assertIsNone(sample["resources"])
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["failed_samples"], 0)
        self.assertEqual(summary["max_report_age_seconds"], 2)
        self.assertEqual(summary["max_radio_age_seconds"], {"bluetooth": 4, "wifi": 5})
        self.assertTrue(summary["checks_passed"])
        self.assertIsNone(summary["max_rss_kib"])
        output = json.dumps(records)
        for private in ("test-key-must-not-appear", "02:12:34:56:78:90", "Private network", "private error text", "observations"):
            self.assertNotIn(private, output)

    def test_explicit_pilot_selection_ignores_waiting_uninstalled_nodes(self):
        payload = dashboard(9)
        for node in payload["nodes"][2:]:
            node.update(state="waiting", age=None, heartbeat_age=None)
            for info in node["radios"].values():
                info.update(state="waiting", age=None)
        status, records, _ = self.run_fake(payload, node_ids=["pi-01", "pi-02"])
        self.assertEqual(status, 0)
        self.assertEqual(records[0]["configured_node_count"], 9)
        self.assertEqual(records[0]["node_count"], 2)
        self.assertEqual([node["id"] for node in records[0]["nodes"]], ["pi-01", "pi-02"])
        payload["nodes"][1]["state"] = "offline"
        status, records, _ = self.run_fake(payload, node_ids=["pi-01", "pi-02"])
        self.assertEqual(status, 1)
        self.assertIn("pi-02:offline", records[0]["errors"])

    def test_unknown_selected_node_fails(self):
        status, records, _ = self.run_fake(node_ids=["pi-01", "pi-99"])
        self.assertEqual(status, 1)
        self.assertIn("pi-99:missing", records[0]["errors"])
        self.assertIn("node_count_mismatch", records[0]["errors"])

    def test_offline_waiting_error_and_wrong_count_fail_but_disabled_is_allowed(self):
        for category in ("offline", "waiting", "stale", "error", "count"):
            with self.subTest(category=category):
                payload = dashboard()
                if category in ("offline", "waiting"):
                    payload["nodes"][0]["state"] = category
                elif category != "count":
                    payload["nodes"][0]["radios"]["bluetooth"]["state"] = category
                status, records, _ = self.run_fake(payload, expected_nodes=3 if category == "count" else 2)
                self.assertEqual(status, 1)
                self.assertEqual(records[-1]["failed_samples"], 3)
                self.assertFalse(records[-1]["checks_passed"])
        payload = dashboard()
        payload["nodes"][0]["radios"]["wifi"].update(state="disabled", age=None)
        status, records, _ = self.run_fake(payload)
        self.assertEqual(status, 0)
        self.assertEqual(records[0]["disabled_radios"], {"bluetooth": 0, "wifi": 1})
        self.assertEqual(records[-1]["max_disabled_radios"]["wifi"], 1)

    def test_request_failure_recovers_but_run_records_failure(self):
        replies = iter([URLError("private server detail"), dashboard(), dashboard()])
        def fetch(url, timeout):
            value = next(replies)
            if isinstance(value, Exception):
                raise value
            return value
        status, records, _ = self.run_fake(fetch=fetch)
        self.assertEqual(status, 1)
        self.assertEqual(records[0]["errors"], ["request_failed"])
        self.assertEqual(records[1]["errors"], [])
        self.assertEqual(records[-1]["request_failures"], 1)
        self.assertEqual(records[-1]["samples"], 3)
        self.assertNotIn("private server detail", json.dumps(records))

    def test_http_error_closes_response_and_does_not_print_body(self):
        response = io.BytesIO(b"private server message")
        error = HTTPError("http://localhost/api/dashboard", 503, "Unavailable", {}, response)
        def fetch(url, timeout):
            raise error
        status, records, _ = self.run_fake(fetch=fetch, duration=1)
        self.assertEqual(status, 1)
        self.assertTrue(response.closed)
        self.assertEqual(records[-1]["request_failures"], 1)
        self.assertNotIn("private server message", json.dumps(records))

    def test_malformed_responses_do_not_crash_or_print_content(self):
        bad_age = dashboard()
        bad_age["nodes"][0]["age"] = float("nan")
        missing_radio = dashboard()
        del missing_radio["nodes"][0]["radios"]["wifi"]
        for payload in ([], {"nodes": "private-invalid-value"}, {"nodes": [None]}, bad_age, missing_radio):
            with self.subTest(payload=payload):
                status, records, _ = self.run_fake(payload, duration=1)
                self.assertEqual(status, 1)
                self.assertEqual(records[0]["errors"], ["invalid_response"])
                self.assertEqual(records[-1]["invalid_responses"], 1)
                self.assertNotIn("private-invalid-value", json.dumps(records))
        def fetch(url, timeout):
            raise ValueError("private bad JSON")
        status, records, _ = self.run_fake(fetch=fetch, duration=1)
        self.assertEqual(status, 1)
        self.assertEqual(records[0]["errors"], ["invalid_response"])

    def test_fetch_timeout_shrinks_to_remaining_duration(self):
        clock = FakeClock()
        timeouts, records = [], []
        def fetch(url, timeout):
            timeouts.append(timeout)
            clock.now += timeout
            raise TimeoutError
        status = monitor.run_monitor("http://localhost/api/dashboard", 4, 1, 2,
                                     fetch=fetch, clock=clock, sleep=clock.sleep, write=records.append)
        self.assertEqual(status, 1)
        self.assertEqual(timeouts, [3, 1])
        self.assertEqual(records[-1]["elapsed_seconds"], 4)
        self.assertEqual(records[-1]["samples"], 2)

    def test_process_resources_have_min_max_and_cpu_delta(self):
        metrics = iter([{"rss_kib": rss, "cpu_seconds": cpu, "start_ticks": 42}
                        for rss, cpu in ((1200, 1), (1100, 2.5), (1600, 4))])
        status, records, _ = self.run_fake(pid=1234, resource_reader=lambda pid: next(metrics))
        summary = records[-1]
        self.assertEqual(status, 0)
        self.assertEqual(summary["min_rss_kib"], 1100)
        self.assertEqual(summary["max_rss_kib"], 1600)
        self.assertEqual(summary["cpu_seconds_used"], 3)
        self.assertEqual(records[0]["resources"], {"rss_kib": 1200, "cpu_seconds": 1})

    def test_missing_or_replaced_process_does_not_pass_resource_check(self):
        def unavailable(pid):
            raise OSError("process missing")
        status, records, _ = self.run_fake(pid=1234, resource_reader=unavailable, duration=1)
        self.assertEqual(status, 1)
        self.assertEqual(records[0]["errors"], ["process_metrics_unavailable"])
        self.assertIsNone(records[0]["resources"])
        metrics = iter([{"rss_kib": 1024, "cpu_seconds": 1, "start_ticks": tick} for tick in (1, 2, 2)])
        status, records, _ = self.run_fake(pid=1234, resource_reader=lambda pid: next(metrics))
        self.assertEqual(status, 1)
        self.assertIn("process_metrics_unavailable", records[1]["errors"])

    def test_process_stat_parser_handles_spaces_and_parentheses(self):
        fields = ["S"] + ["0"] * 21
        for index, value in ((11, "250"), (12, "50"), (19, "600"), (21, "100")):
            fields[index] = value
        text = "4321 (python worker ) name) " + " ".join(fields)
        self.assertEqual(monitor.parse_process_stat(text, 100, 4096),
                         {"rss_kib": 400, "cpu_seconds": 3, "start_ticks": 600})
        for malformed in ("", "123 (x) S 1 2", text.replace("250", "broken"), text.replace("250", "-1")):
            with self.subTest(text=malformed), self.assertRaises(ValueError):
                monitor.parse_process_stat(malformed, 100, 4096)

    def test_ctrl_c_emits_summary_and_returns_130(self):
        def interrupted_sleep(seconds):
            raise KeyboardInterrupt
        status, records, _ = self.run_fake(sleep=interrupted_sleep)
        self.assertEqual(status, 130)
        self.assertEqual(records[-1]["type"], "summary")
        self.assertTrue(records[-1]["interrupted"])
        self.assertFalse(records[-1]["checks_passed"])
        self.assertEqual(records[-1]["samples"], 1)

    def test_cli_rejects_invalid_values_and_passes_pilot_selection(self):
        for flag, value in (("--duration", "nan"), ("--duration", "0"), ("--interval", "inf"),
                            ("--interval", "-2"), ("--expected-nodes", "0"), ("--pid", "1.5")):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                monitor.main([flag, value])
            self.assertEqual(raised.exception.code, 2)
        with patch.object(monitor, "run_monitor", return_value=0) as run:
            self.assertEqual(monitor.main(["--node", "pi-01", "--node", "pi-02", "--expected-nodes", "2"]), 0)
        self.assertEqual(run.call_args.kwargs["node_ids"], ["pi-01", "pi-02"])


if __name__ == "__main__":
    unittest.main()
