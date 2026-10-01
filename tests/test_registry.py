import json
from pathlib import Path
import tempfile
import threading
import unittest

from registry import MAX_OBSERVATIONS, Registry, ReportError, load_config


def configuration():
    return {
        "nodes": [
            {"id": f"pi-{i:02d}", "label": f"Pi {i}", "floor": i, "location": f"Room {i}", "x": 25, "y": 50, "api_key": f"test-only-node-key-{i}"}
            for i in range(1, 4)
        ]
    }


def observation(path="/test/1", **properties):
    return {"path": path, "properties": properties}


def scan(node="pi-01", radio="bluetooth", records=None):
    return {"node_id": node, "kind": "scan", "radio": radio, "status": "success", "observations": [] if records is None else records}


def heartbeat(node="pi-01", enabled=None):
    return {"node_id": node, "kind": "heartbeat", "enabled_radios": ["bluetooth", "wifi"] if enabled is None else enabled}


class ConfigTests(unittest.TestCase):
    def test_load_defaults_and_public_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server.json"
            path.write_text(json.dumps(configuration()), encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config["freshness"], {"node": 60, "bluetooth": 60, "wifi": 150})
        nodes = Registry(config).snapshot()["nodes"]
        self.assertEqual([n["floor"] for n in nodes], [1, 2, 3])
        self.assertEqual(nodes[0]["location"], "Room 1")
        self.assertNotIn("api_key", json.dumps(nodes))
        self.assertNotIn("test-only-node-key", json.dumps(nodes))

    def test_missing_and_invalid_file_errors_are_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server.json"
            with self.assertRaisesRegex(ValueError, "Cannot read server configuration"):
                load_config(path)
            for content in (b"{ not json", b"\xff"):
                path.write_bytes(content)
                with self.assertRaisesRegex(ValueError, "valid UTF-8 JSON"):
                    load_config(path)

    def test_invalid_configuration(self):
        changes = [
            ("id", None), ("id", "has spaces"), ("id", "pi-02"),
            ("api_key", "test-only-node-key-2"), ("api_key", ""), ("api_key", "two words"),
            ("api_key", "short"), ("api_key", "test-only-node-key-\x7f"),
            ("label", ""), ("location", None), ("floor", 0), ("floor", 4), ("floor", True),
            ("x", -1), ("x", 101), ("x", float("nan")), ("y", float("inf")), ("x", True), ("x", 10 ** 400),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                config = configuration()
                config["nodes"][0][field] = value
                with self.assertRaises(ValueError) as caught:
                    Registry(config)
                self.assertNotIn("test-only-node-key", str(caught.exception))
        for config in (None, [], {}, {"nodes": []}, {"nodes": [None]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                Registry(config)

    def test_custom_freshness_and_reject_invalid_limits(self):
        for invalid in (0, -1, True, None, "10", float("nan"), float("inf")):
            config = configuration()
            config["freshness"] = {"wifi": invalid}
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                Registry(config)
        for invalid in (None, {"typo": 1}):
            config = configuration()
            config["freshness"] = invalid
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                Registry(config)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.registry = Registry(configuration(), clock=lambda: self.now)

    def accept(self, report, source_ip="192.0.2.1"):
        self.registry.accept(report, f"test-only-node-key-{int(report['node_id'][-2:])}", source_ip)

    def node(self, node_id="pi-01"):
        return next(n for n in self.registry.snapshot()["nodes"] if n["id"] == node_id)

    def radio(self, name="bluetooth", node_id="pi-01"):
        return self.node(node_id)["radios"][name]

    def test_initial_waiting_and_heartbeat_do_not_invent_observations(self):
        node = self.node()
        self.assertEqual(node["state"], "waiting")
        self.assertIsNone(node["age"])
        self.assertIsNone(node["heartbeat_age"])
        self.assertEqual(self.radio()["state"], "waiting")
        self.assertIsNone(self.radio()["count"])
        self.accept(heartbeat())
        self.assertEqual(self.node()["state"], "online")
        self.assertEqual(self.node()["heartbeat_age"], 0)
        self.assertEqual(self.radio()["state"], "waiting")

    def test_snapshot_replacement_and_empty_are_per_radio(self):
        records = [observation("/one", Address="AA", RSSI=-31), observation("/two", Address="BB")]
        self.accept(scan(records=records))
        self.accept(scan(radio="wifi", records=[observation(BSSID="CC", Strength=70)]))
        self.assertEqual(self.radio()["count"], 2)
        self.accept(scan(records=records))
        self.assertEqual(self.radio()["count"], 2)
        self.accept(scan())
        self.assertEqual(self.radio()["state"], "fresh")
        self.assertEqual(self.radio()["count"], 0)
        self.assertEqual(self.radio("wifi")["count"], 1)

    def test_source_ip_change_keeps_identity_and_position(self):
        self.accept(heartbeat(), "192.0.2.1")
        before = self.node()
        self.accept(heartbeat(), "192.0.2.200")
        after = self.node()
        self.assertEqual(after["ip"], "192.0.2.200")
        for field in ("id", "floor", "location", "x", "y"):
            self.assertEqual(before[field], after[field])

    def test_errors_retain_last_good_data_but_exclude_current_totals(self):
        records = [observation(BSSID="AA:BB", Strength=30)]
        self.accept(scan(radio="wifi", records=records))
        self.now += 4
        error = {"node_id": "pi-01", "kind": "scan", "radio": "wifi", "status": "error", "error": "Permission denied"}
        self.accept(error)
        radio = self.radio("wifi")
        self.assertEqual(radio["state"], "error")
        self.assertIsNone(radio["count"])
        self.assertEqual(radio["observations"], records)
        self.assertEqual(radio["age"], 4)
        self.assertEqual(self.registry.snapshot()["all_observations"]["wifi"], [])
        self.accept(scan(radio="wifi"))
        self.assertEqual(self.radio("wifi")["count"], 0)
        self.assertIsNone(self.radio("wifi")["error"])

    def test_error_before_first_success_is_not_empty_success(self):
        self.accept({"node_id": "pi-01", "kind": "scan", "radio": "bluetooth", "status": "error", "error": "No adapter"})
        self.assertEqual(self.radio()["state"], "error")
        self.assertIsNone(self.radio()["age"])
        self.assertIsNone(self.radio()["count"])

    def test_heartbeat_and_other_radio_never_refresh_detection_age(self):
        self.accept(scan(records=[observation(Address="AA")]))
        self.accept(scan(radio="wifi", records=[observation(BSSID="BB")]))
        self.now += 59.99
        self.accept(heartbeat())
        self.assertEqual(self.radio()["state"], "fresh")
        self.now = 1060
        self.accept(scan(radio="wifi", records=[observation(BSSID="BB")]))
        self.assertEqual(self.node()["state"], "online")
        self.assertEqual(self.radio()["state"], "stale")
        self.assertEqual(self.radio()["age"], 60)
        self.assertEqual(self.radio("wifi")["age"], 0)
        self.assertEqual(self.radio("wifi")["state"], "fresh")

    def test_wifi_150_second_freshness_boundary(self):
        self.accept(scan(radio="wifi", records=[observation(BSSID="AA")]))
        self.now = 1149.99
        self.accept(heartbeat())
        self.assertEqual(self.radio("wifi")["state"], "fresh")
        self.now = 1150
        self.assertEqual(self.radio("wifi")["state"], "stale")
        self.assertIsNone(self.radio("wifi")["count"])

    def test_offline_at_60_seconds_and_only_silent_node_is_affected(self):
        self.accept(scan(records=[observation(Address="AA")]))
        self.now += 59.99
        self.assertEqual(self.node()["state"], "online")
        self.now = 1060
        self.accept(scan(node="pi-02", records=[observation(Address="BB")]))
        self.assertEqual(self.node()["state"], "offline")
        self.assertEqual(self.radio()["state"], "offline")
        self.assertIsNone(self.radio()["count"])
        self.assertEqual(len(self.radio()["observations"]), 1)
        self.assertEqual(self.node("pi-02")["state"], "online")
        self.assertEqual(self.node("pi-03")["state"], "waiting")
        groups = self.registry.snapshot()["all_observations"]["bluetooth"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["observations"][0]["node_id"], "pi-02")
        self.accept(heartbeat())
        self.assertEqual(self.node()["state"], "online")
        self.assertEqual(self.radio()["state"], "stale")
        self.accept(scan(records=[observation(Address="AA")]))
        self.assertEqual(self.radio()["state"], "fresh")

    def test_disabled_excludes_previous_results_and_has_priority(self):
        self.accept(scan(records=[observation(Address="AA")]))
        self.accept(heartbeat(enabled=["wifi"]))
        self.assertEqual(self.radio()["state"], "disabled")
        self.assertIsNone(self.radio()["count"])
        self.assertEqual(len(self.radio()["observations"]), 1)
        self.assertEqual(self.registry.snapshot()["all_observations"]["bluetooth"], [])
        self.now += 60
        self.assertEqual(self.node()["state"], "offline")
        self.assertEqual(self.radio()["state"], "disabled")
        self.assertEqual(self.radio("wifi")["state"], "offline")

    def test_restart_restores_layout_not_observations(self):
        self.accept(scan(records=[observation(Address="AA")]))
        self.registry = Registry(configuration(), clock=lambda: self.now)
        self.assertEqual(self.node()["state"], "waiting")
        self.accept(heartbeat())
        self.assertEqual(self.node()["state"], "online")
        self.assertEqual(self.radio()["state"], "waiting")
        self.assertEqual(self.radio()["observations"], [])

    def test_reenable_waits_for_new_scan_before_restoring_freshness(self):
        self.accept(scan(records=[observation(Address="AA")]))
        self.accept(heartbeat(enabled=[]))
        self.accept(heartbeat())
        self.assertEqual(self.radio()["state"], "stale")
        self.assertEqual(self.radio()["age"], 0)
        self.assertIsNone(self.radio()["count"])
        self.assertEqual(len(self.radio()["observations"]), 1)
        self.assertEqual(self.registry.snapshot()["all_observations"]["bluetooth"], [])
        self.accept(scan(records=[observation(Address="AA")]))
        self.assertEqual(self.radio()["state"], "fresh")
        self.accept(heartbeat(enabled=[]))
        self.accept(scan(records=[observation(Address="BB")]))
        self.accept(heartbeat())
        self.assertEqual(self.radio()["state"], "stale")
        self.assertEqual(self.radio()["observations"][0]["properties"]["Address"], "BB")

    def test_custom_limits_and_receipt_clock_only(self):
        config = configuration()
        config["freshness"] = {"node": 10, "bluetooth": 3}
        self.registry = Registry(config, clock=lambda: self.now)
        self.accept(scan())
        self.now += 3
        self.assertEqual(self.radio()["state"], "stale")
        self.assertEqual(self.node()["state"], "online")
        self.now += 7
        self.assertEqual(self.node()["state"], "offline")

    def test_inputs_and_output_are_detached(self):
        config = configuration()
        self.registry = Registry(config, clock=lambda: self.now)
        config["nodes"][0]["label"] = "Changed"
        report = scan(records=[observation(Address="AA", ManufacturerData={"0x0001": [1, 2]})])
        self.accept(report)
        report["observations"][0]["properties"]["ManufacturerData"]["0x0001"].append(3)
        first = self.registry.snapshot()
        first["nodes"][0]["radios"]["bluetooth"]["observations"].clear()
        first["all_observations"]["bluetooth"][0]["observations"][0]["properties"]["Address"] = "Changed"
        fresh = self.node()
        self.assertEqual(fresh["label"], "Pi 1")
        self.assertEqual(fresh["radios"]["bluetooth"]["observations"][0]["properties"], {"Address": "AA", "ManufacturerData": {"0x0001": [1, 2]}})

    def test_authentication_statuses_and_rejected_reports_do_not_refresh_state(self):
        self.accept(scan(records=[observation(Address="AA")]))
        self.now += 20
        before = self.registry.snapshot()
        for report, token, status in (
            (heartbeat(), None, 401), (heartbeat(), "", 401),
            (heartbeat(), "wrong", 403), (heartbeat(), "test-only-node-key-2", 403),
            (heartbeat(node="unregistered"), "test-only-node-key-1", 403),
            (heartbeat(), "non-ascii-\ud800", 403),
        ):
            with self.subTest(status=status), self.assertRaises(ReportError) as caught:
                self.registry.accept(report, token, "192.0.2.2")
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn("test-only-node-key", str(caught.exception))
        self.assertEqual(before, self.registry.snapshot())

    def test_invalid_payloads_leave_latest_snapshot_untouched(self):
        self.accept(scan(records=[observation(Address="AA")]))
        self.now += 20
        before = self.registry.snapshot()
        bad = [
            {"node_id": "pi-01"}, {"node_id": "pi-01", "kind": "unknown"},
            heartbeat(enabled=["unknown"]), heartbeat(enabled=["wifi", "wifi"]), heartbeat(enabled="wifi"),
            heartbeat(enabled=[{}]), scan(radio="unknown"), scan(records="wrong"),
            scan(records=[{"path": "/one"}]), scan(records=[observation(path="")]),
            scan(records=[observation(), observation()]),
            scan(records=[{"path": "/one", "properties": []}]),
            scan(records=[observation(Data=float("nan"))]), scan(records=[observation(Data=float("inf"))]),
            scan(records=[observation(Data=10 ** 400)]), scan(records=[observation(Data=b"raw")]),
            scan(records=[observation(Data={1: "not a string key"})]),
            scan(records=[observation(Data="a" * 16385)]),
            scan(records=[observation(**{f"p{i}": i for i in range(257)})]),
            scan(records=[observation(path=f"/record/{i}") for i in range(MAX_OBSERVATIONS + 1)]),
            {"node_id": "pi-01", "kind": "scan", "radio": "wifi", "status": "error", "error": ""},
            {"node_id": "pi-01", "kind": "scan", "radio": "wifi", "status": "error", "error": "e" * 501},
            {**scan(), "error": "ambiguous"}, {**heartbeat(), "unexpected": True},
        ]
        nested = []
        for _ in range(10):
            nested = [nested]
        bad.append(scan(records=[observation(Data=nested)]))
        bad.append(scan(records=[observation(Data=[None] * 32768)]))
        bad.append(scan(records=[observation(path=f"/{i}", Data="x" * 16000) for i in range(70)]))
        for i, report in enumerate(bad):
            with self.subTest(case=i), self.assertRaises(ReportError) as caught:
                self.registry.accept(report, "test-only-node-key-1", "192.0.2.99")
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(before, self.registry.snapshot())
        for report in (None, [], {}, {"node_id": []}):
            with self.subTest(report=report), self.assertRaises(ReportError):
                self.registry.accept(report, "test-only-node-key-1", "192.0.2.99")

    def test_unknown_properties_are_preserved(self):
        records = [observation(Name="<script>plain text</script>", Unknown=[None, True, 1.5, {"Nested": "00ff"}])]
        self.accept(scan(records=records))
        self.assertEqual(self.radio()["observations"], records)
        json.dumps(self.registry.snapshot(), allow_nan=False)

    def test_bluetooth_grouping_keeps_address_type_and_node_signals(self):
        self.accept(scan(records=[observation(Address="AA:BB", AddressType="public", RSSI=-30), observation("/missing", Name="Unidentified")]))
        self.accept(scan(node="pi-02", records=[observation(Address="aa:bb", AddressType="PUBLIC", RSSI=-70), observation("/missing", Name="Unidentified"), observation("/random", Address="AA:BB", AddressType="random")]))
        groups = self.registry.snapshot()["all_observations"]["bluetooth"]
        self.assertEqual(len(groups), 4)
        shared = next(group for group in groups if len(group["observations"]) == 2)
        self.assertEqual([(r["node_id"], r["properties"]["RSSI"]) for r in shared["observations"]], [("pi-01", -30), ("pi-02", -70)])

    def test_wifi_grouping_uses_bssid_not_ssid_or_bluetooth_identity(self):
        self.accept(scan(radio="wifi", records=[observation(BSSID="AA:BB", SSID="Same", Strength=80), observation("/two", BSSID="CC:DD", SSID="Same", Strength=60)]))
        self.accept(scan(node="pi-02", radio="wifi", records=[observation(BSSID="aa:bb", SSID="Same", Strength=20)]))
        self.accept(scan(records=[observation(Address="AA:BB", RSSI=-10)]))
        all_records = self.registry.snapshot()["all_observations"]
        self.assertEqual(len(all_records["wifi"]), 2)
        self.assertEqual(len(all_records["bluetooth"]), 1)
        shared = next(group for group in all_records["wifi"] if len(group["observations"]) == 2)
        self.assertEqual([r["properties"]["Strength"] for r in shared["observations"]], [80, 20])

    def test_hardware_address_fallback_and_missing_wifi_address(self):
        self.accept(scan(radio="wifi", records=[observation(HwAddress="AA:BB"), observation("/no-address")]))
        self.accept(scan(node="pi-02", radio="wifi", records=[observation(BSSID="aa:bb"), observation("/no-address")]))
        groups = self.registry.snapshot()["all_observations"]["wifi"]
        self.assertEqual(len(groups), 3)

    def test_concurrent_reports_and_polling_remain_consistent(self):
        errors = []
        barrier = threading.Barrier(3)

        def writer(node_id):
            try:
                barrier.wait(timeout=2)
                for count in range(1, 51):
                    self.accept(scan(node=node_id, records=[observation(path=f"/{i}", Generation=count) for i in range(count)]))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(node,)) for node in ("pi-01", "pi-02")]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for _ in range(75):
            snapshot = self.registry.snapshot()
            for node in snapshot["nodes"][:2]:
                radio = node["radios"]["bluetooth"]
                if radio["state"] == "fresh":
                    self.assertEqual(radio["count"], len(radio["observations"]))
                    self.assertTrue(all(r["properties"]["Generation"] == radio["count"] for r in radio["observations"]))
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.radio()["count"], 50)
        self.assertEqual(self.radio(node_id="pi-02")["count"], 50)


if __name__ == "__main__":
    unittest.main()
