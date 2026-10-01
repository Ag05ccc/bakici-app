import argparse
import asyncio
import contextlib
import io
import json
import os
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dbus_fast import Message, Variant
from dbus_fast.errors import AuthError

import scan_wifi as wifi


OWNER = ":1.71"
DEVICE_PATH = wifi.NM_PATH + "/Devices/2"
AP_PATH = wifi.NM_PATH + "/AccessPoint/1"


def ap(bssid="02:00:00:00:00:01", ssid=b"Example", seen=101, strength=71, **extras):
    props = {"HwAddress": Variant("s", bssid), "Ssid": Variant("ay", ssid),
             "LastSeen": Variant("i", seen), "Frequency": Variant("u", 2412),
             "Strength": Variant("y", strength), "Flags": Variant("u", 1),
             "WpaFlags": Variant("u", 0), "RsnFlags": Variant("u", 0x100)}
    props.update(extras)
    return props


def device(interface="wlan0", managed=True, device_type=2):
    return {"Interface": Variant("s", interface), "Managed": Variant("b", managed),
            "DeviceType": Variant("u", device_type)}


class FakeBus:
    def __init__(self, *, aps=None, devices=None, complete=True, on_request=None,
                 errors=None, stall=None, on_call=None):
        self.aps = {AP_PATH: ap()} if aps is None else aps
        self.devices = {DEVICE_PATH: device()} if devices is None else devices
        self.complete = complete
        self.on_request = on_request
        self.on_call = on_call
        self.errors = errors or {}
        self.stall = stall
        self.last_scan = 99000
        self.manager = {"WirelessEnabled": Variant("b", True), "WirelessHardwareEnabled": Variant("b", True)}
        self.handler = None
        self.connected = False
        self.disconnected = asyncio.Event()
        self.requested = asyncio.Event()
        self.entered = asyncio.Event()
        self.calls = []

    async def connect(self):
        if self.stall == "connect":
            self.entered.set()
            await asyncio.Event().wait()
        self.connected = True
        return self

    def add_message_handler(self, handler):
        self.handler = handler

    def remove_message_handler(self, handler):
        self.handler = None

    def emit(self, path, interface, changed=None, invalidated=None, sender=OWNER):
        message = Message.new_signal(path, wifi.PROPERTIES, "PropertiesChanged", "sa{sv}as",
                                     [interface, changed or {}, invalidated or []])
        message.sender = sender
        self.handler(message)

    def finish(self, last_scan=101000, *, invalidated=False, sender=OWNER):
        self.last_scan = last_scan
        self.emit(DEVICE_PATH, wifi.WIRELESS,
                  {} if invalidated else {"LastScan": Variant("x", last_scan)},
                  ["LastScan"] if invalidated else [], sender=sender)

    async def call(self, request):
        self.calls.append(request)
        request.serial = len(self.calls)
        if self.on_call:
            self.on_call(self, request)
        if request.member == self.stall or (self.stall == "read" and request.path in self.aps):
            self.entered.set()
            await asyncio.Event().wait()
        error = self.errors.get((request.path, request.member), self.errors.get(request.member))
        if error:
            return Message.new_error(request, error, "simulated failure")
        if request.member == "GetNameOwner":
            return Message.new_method_return(request, "s", [OWNER])
        if request.member == "GetDevices":
            return Message.new_method_return(request, "ao", [list(self.devices)])
        if request.member == "GetAll":
            if request.path == wifi.NM_PATH:
                props = self.manager
            elif request.path in self.devices:
                props = self.devices[request.path]
            else:
                props = self.aps[request.path]
            return Message.new_method_return(request, "a{sv}", [props])
        if request.member == "Get":
            return Message.new_method_return(request, "v", [Variant("x", self.last_scan)])
        if request.member == "RequestScan":
            self.requested.set()
            if self.on_request:
                self.on_request(self)
            if self.complete:
                self.finish()
        if request.member == "GetAllAccessPoints":
            return Message.new_method_return(request, "ao", [list(self.aps)])
        return Message.new_method_return(request)

    def disconnect(self):
        self.connected = False
        self.disconnected.set()

    async def wait_for_disconnect(self):
        await self.disconnected.wait()

    def members(self):
        return [request.member for request in self.calls]


class PropertyTests(unittest.TestCase):
    def test_fields_hidden_and_non_utf8_ssid_preserve_original_bytes(self):
        record = wifi.access_point_record(AP_PATH, ap(ssid=b"\xff<sensor>\x00"))
        props = record["properties"]
        self.assertEqual(props["SSIDHex"], "ff3c73656e736f723e00")
        self.assertEqual(props["Ssid"], props["SSIDHex"])
        self.assertEqual(props["SSID"], "\ufffd<sensor>\x00")
        self.assertEqual(props["Channel"], 1)
        hidden = wifi.access_point_record(AP_PATH, ap(ssid=b""))
        self.assertEqual(hidden["properties"]["SSID"], "")
        self.assertIn("(Hidden)", wifi.format_results([hidden]))

    def test_unknown_and_binary_values_are_json_compatible(self):
        record = wifi.access_point_record(AP_PATH, {"Vendor": Variant("ay", b"\x00\xff")})
        self.assertIsNone(record["properties"]["SSID"])
        self.assertIsNone(record["properties"]["Security"])
        self.assertEqual(record["properties"]["Vendor"], "00ff")
        self.assertIn("Unknown", wifi.format_results([record]))
        self.assertEqual(json.loads(wifi.format_results([record], True)), [record])
        self.assertEqual(wifi.normalize({"nested": Variant("aay", [b"\x12", b"\x34"])}), {"nested": ["12", "34"]})

    def test_channels_include_boundaries_and_reject_unrecognized_frequencies(self):
        for frequency, channel in ((2412, 1), (2472, 13), (2484, 14), (5180, 36),
                                   (5885, 177), (5935, 2), (5955, 1), (7115, 233),
                                   (2413, None), (5000, None), (9999, None), (None, None)):
            with self.subTest(frequency=frequency):
                self.assertEqual(wifi.channel_for_frequency(frequency), channel)

    def test_security_flags_are_advertised_not_guessed_from_name(self):
        for flags, wpa, rsn, expected in (
            (0, 0, 0, "Open"), (1, 0, 0, "WEP/privacy"), (1, 0x100, 0, "WPA"),
            (1, 0, 0x500, "WPA2 Personal, WPA3 Personal"),
            (1, 0, 0x200, "WPA2/RSN Enterprise"), (0, 0, 0x800, "Enhanced Open (OWE)"),
            (0, 0, 0x1000, "OWE transition"), (1, 0, 0x2000, "WPA3 Enterprise Suite-B"),
            (1, 0, 0x8, "RSN (authentication unknown)"), (None, 0, 0, None),
        ):
            with self.subTest(rsn=rsn, wpa=wpa, flags=flags):
                self.assertEqual(wifi.advertised_security({"Flags": flags, "WpaFlags": wpa, "RsnFlags": rsn}), expected)

    def test_freshness_one_second_tolerance_and_unknown(self):
        for seen, expected in ((98, False), (99, True), (100, True), (101, True),
                               (102, True), (103, False), (-1, False), (None, False)):
            self.assertEqual(wifi.is_fresh(seen, 100, 101), expected)
        self.assertFalse(wifi.is_fresh(99, 100.1, 101))

    def test_signal_sorting_and_terminal_safety(self):
        records = [wifi.access_point_record(AP_PATH, {}),
                   wifi.access_point_record(AP_PATH + "b", ap(strength=0)),
                   wifi.access_point_record(AP_PATH + "c", ap(ssid=b"x\n\x1b[2J", strength=100))]
        ordered = sorted(records, key=wifi.signal_sort_key)
        self.assertEqual([record["properties"]["Strength"] for record in ordered], [100, 0, None])
        output = wifi.format_results(ordered)
        self.assertNotIn("\x1b", output)
        self.assertIn("Signal (%)", output)
        self.assertNotIn("dBm", output)
        self.assertIn("advertised capability", output)
        self.assertEqual(wifi.format_results([], True), "[]")
        self.assertEqual(wifi.format_results([]), "No Wi-Fi access points discovered.")

    def test_invalid_cli_timeouts(self):
        for value in ("0", "-1", "nan", "inf", "-inf", "x"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                wifi.positive_timeout(value)
        self.assertEqual(wifi.positive_timeout("0.1"), 0.1)


class ScannerTests(unittest.IsolatedAsyncioTestCase):
    async def scan(self, bus, **kwargs):
        return await wifi.scan(kwargs.pop("timeout", 0.2), bus_factory=lambda **_: bus,
                               clock=lambda: 100, **kwargs)

    def assert_clean(self, bus):
        self.assertFalse(bus.connected)
        self.assertIsNone(bus.handler)
        self.assertTrue(bus.disconnected.is_set())

    async def test_completion_before_request_reply_is_not_missed(self):
        bus = FakeBus()
        result = await self.scan(bus)
        self.assertEqual(len(result), 1)
        self.assertLess(bus.members().index("AddMatch"), bus.members().index("RequestScan"))
        self.assertLess(bus.members().index("RequestScan"), bus.members().index("GetAllAccessPoints"))
        self.assert_clean(bus)

    async def test_accepted_request_waits_for_completion_and_invalidation_is_supported(self):
        bus = FakeBus(complete=False)
        task = asyncio.create_task(self.scan(bus))
        await bus.requested.wait()
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        self.assertNotIn("GetAllAccessPoints", bus.members())
        bus.finish(invalidated=True)
        self.assertEqual(len(await task), 1)
        self.assert_clean(bus)

    async def test_old_completion_is_not_a_new_scan(self):
        bus = FakeBus(complete=False)
        task = asyncio.create_task(self.scan(bus))
        await bus.requested.wait()
        bus.finish(99500)
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        bus.finish()
        self.assertEqual(len(await task), 1)

    async def test_filters_cache_and_merges_bssid_not_ssid(self):
        aps = {AP_PATH + str(i): ap(bssid=f"02:00:00:00:00:{i:02x}", seen=seen)
               for i, seen in enumerate((98, 99, 100, 101, 102, 103, -1))}
        aps[AP_PATH + "duplicate"] = ap(bssid="02:00:00:00:00:03", seen=102, strength=90)
        aps[AP_PATH + "unknown"] = {"Ssid": Variant("ay", b"Cache")}
        records = await self.scan(FakeBus(aps=aps))
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]["properties"]["BSSID"], "02:00:00:00:00:03")
        self.assertEqual(records[0]["properties"]["Strength"], 90)
        self.assertEqual({record["properties"]["SSID"] for record in records}, {"Example"})

    async def test_successful_empty_is_distinct_from_timeout(self):
        self.assertEqual(await self.scan(FakeBus(aps={})), [])
        bus = FakeBus(complete=False)
        with self.assertRaisesRegex(wifi.ScanError, "timed out"):
            await self.scan(bus, timeout=0.02)
        self.assert_clean(bus)

    async def test_interface_selection_and_managed_filter(self):
        paths = {DEVICE_PATH + "b": device("wlan1"), DEVICE_PATH: device("wlan0"),
                 DEVICE_PATH + "c": device("aaa", managed=False)}
        for interface, expected in ((None, DEVICE_PATH), ("wlan1", DEVICE_PATH + "b")):
            # Fake completion signal uses default path; the post-ack property read
            # deliberately proves completion even if that signal was not ours.
            bus = FakeBus(devices=paths)
            await self.scan(bus, interface=interface)
            requested = next(call for call in bus.calls if call.member == "RequestScan")
            self.assertEqual(requested.path, expected)

    async def test_missing_unmanaged_configured_interface_and_disabled_wifi(self):
        cases = [(FakeBus(devices={}), None, "No Wi-Fi adapter"),
                 (FakeBus(devices={DEVICE_PATH: device(managed=False)}), None, "managed"),
                 (FakeBus(), "absent", "not found")]
        disabled = FakeBus()
        disabled.manager["WirelessEnabled"] = Variant("b", False)
        cases.append((disabled, None, "off or blocked"))
        for bus, interface, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(wifi.ScanError, message):
                await self.scan(bus, interface=interface)
            self.assert_clean(bus)

    async def test_service_permission_and_busy_errors_are_clear(self):
        for member, name, expected in (
            ("GetNameOwner", wifi.DBUS + ".Error.NameHasNoOwner", "NetworkManager is unavailable"),
            ("RequestScan", wifi.NM + ".Device.PermissionDenied", "wifi.scan permission"),
            ("RequestScan", wifi.NM + ".Device.NotAllowed", "busy or unavailable"),
        ):
            bus = FakeBus(errors={member: name})
            with self.subTest(name=name), self.assertRaisesRegex(wifi.ScanError, expected):
                await self.scan(bus)
            self.assert_clean(bus)

    async def test_dbus_disconnection_during_scan_is_failure(self):
        bus = FakeBus(complete=False, on_request=lambda bus: bus.disconnect())
        with self.assertRaisesRegex(wifi.ScanError, "Lost connection"):
            await self.scan(bus)
        self.assert_clean(bus)

    async def test_service_restart_and_wifi_off_are_failure(self):
        def service_loss(bus):
            message = Message.new_signal(wifi.DBUS_PATH, wifi.DBUS, "NameOwnerChanged", "sss", [wifi.NM, OWNER, ":1.72"])
            message.sender = wifi.DBUS
            bus.handler(message)

        for callback, expected in ((service_loss, "stopped or restarted"),
                                   (lambda bus: bus.emit(wifi.NM_PATH, wifi.NM, {"WirelessEnabled": Variant("b", False)}), "turned off"),
                                   (lambda bus: bus.emit(DEVICE_PATH, wifi.DEVICE, {"Managed": Variant("b", False)}), "no longer managed")):
            bus = FakeBus(complete=False, on_request=callback)
            with self.subTest(expected=expected), self.assertRaisesRegex(wifi.ScanError, expected):
                await self.scan(bus)
            self.assert_clean(bus)

    async def test_untrusted_sender_does_not_complete_scan(self):
        bus = FakeBus(complete=False)
        task = asyncio.create_task(self.scan(bus))
        await bus.requested.wait()
        await asyncio.sleep(0.01)
        bus.emit(DEVICE_PATH, wifi.WIRELESS, {"LastScan": Variant("x", 101000)}, sender=":1.99")
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        bus.finish()
        await task

    async def test_disappearing_ap_is_skipped_but_access_error_fails(self):
        for error, succeeds in ((wifi.DBUS + ".Error.UnknownObject", True),
                               (wifi.DBUS + ".Error.AccessDenied", False)):
            bus = FakeBus(errors={(AP_PATH, "GetAll"): error})
            if succeeds:
                self.assertEqual(await self.scan(bus), [])
            else:
                with self.assertRaisesRegex(wifi.ScanError, "access denied"):
                    await self.scan(bus)
            self.assert_clean(bus)

    async def test_stop_during_connect_request_wait_and_read_cleans_up(self):
        for stage in ("connect", "RequestScan", "wait", "read"):
            bus = FakeBus(stall=None if stage == "wait" else stage, complete=stage != "wait")
            stop = asyncio.Event()
            task = asyncio.create_task(self.scan(bus, stop_event=stop))
            await (bus.requested.wait() if stage == "wait" else bus.entered.wait())
            stop.set()
            self.assertEqual(await task, [])
            self.assert_clean(bus)
        self.assertEqual(len(await self.scan(FakeBus())), 1)

    async def test_task_cancellation_cleans_up_at_all_phases(self):
        for stage in ("connect", "RequestScan", "wait", "read"):
            bus = FakeBus(stall=None if stage == "wait" else stage, complete=stage != "wait")
            task = asyncio.create_task(self.scan(bus))
            await (bus.requested.wait() if stage == "wait" else bus.entered.wait())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assert_clean(bus)

    async def test_overall_deadline_also_bounds_connect_and_property_reads(self):
        for stage in ("connect", "RequestScan", "read"):
            bus = FakeBus(stall=stage)
            with self.assertRaisesRegex(wifi.ScanError, "timed out"):
                await self.scan(bus, timeout=0.02)
            self.assert_clean(bus)

    async def test_stopped_before_start_does_not_connect_or_change_network(self):
        stop = asyncio.Event()
        stop.set()
        bus = FakeBus()
        self.assertEqual(await self.scan(bus, stop_event=stop), [])
        self.assertEqual(bus.calls, [])
        await self.scan(bus)
        self.assertTrue(set(bus.members()) <= {"AddMatch", "GetNameOwner", "GetAll", "Get", "GetDevices", "RequestScan", "GetAllAccessPoints"})

    async def test_partial_fresh_records_are_preserved_when_stop_during_read(self):
        stop = asyncio.Event()
        def stop_on_second(bus, request):
            if request.path == AP_PATH + "b":
                stop.set()
        bus = FakeBus(aps={AP_PATH: ap(), AP_PATH + "b": ap(bssid="02:00:00:00:00:02")}, on_call=stop_on_second)
        records = await self.scan(bus, stop_event=stop)
        self.assertGreaterEqual(len(records), 1)
        self.assert_clean(bus)

    async def test_backend_validation_and_connection_permissions(self):
        for duration in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                await self.scan(FakeBus(), timeout=duration)
        bus = FakeBus()
        bus.connect = AsyncMock(side_effect=AuthError("denied"))
        with self.assertRaisesRegex(wifi.ScanError, "D-Bus was denied"):
            await self.scan(bus)
        self.assert_clean(bus)

    async def test_cli_json_stdout_and_error_exit(self):
        args = SimpleNamespace(timeout=15, interface="wlan0", json=True)
        out, err = io.StringIO(), io.StringIO()
        async def fake_scan(timeout, stop, **kwargs):
            kwargs["on_status"]("Scanning")
            return [wifi.access_point_record(AP_PATH, ap())]
        with patch.object(wifi, "scan", fake_scan), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(await wifi.run(args), 0)
        self.assertEqual(len(json.loads(out.getvalue())), 1)
        self.assertEqual(err.getvalue(), "Scanning\n")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(wifi, "scan", AsyncMock(side_effect=wifi.ScanError("denied"))), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(await wifi.run(args), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("denied", err.getvalue())

    async def test_cli_sigint_returns_partial_json_and_exit_130(self):
        args = SimpleNamespace(timeout=15, interface=None, json=True)
        result = [wifi.access_point_record(AP_PATH, ap())]
        async def fake_scan(timeout, stop, **kwargs):
            asyncio.get_running_loop().call_soon(os.kill, os.getpid(), signal.SIGINT)
            await stop.wait()
            return result
        out, err = io.StringIO(), io.StringIO()
        with patch.object(wifi, "scan", fake_scan), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(await wifi.run(args), 130)
        self.assertEqual(json.loads(out.getvalue()), result)
        self.assertIn("interrupted", err.getvalue())


if __name__ == "__main__":
    unittest.main()
