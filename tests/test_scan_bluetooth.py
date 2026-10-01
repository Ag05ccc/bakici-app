import argparse
import asyncio
import contextlib
import io
import json
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dbus_fast import Message, Variant
from dbus_fast.errors import AuthError

import scan_bluetooth


ADAPTER = "/org/bluez/hci0"
DEVICE = ADAPTER + "/dev_AA_BB_CC_DD_EE_FF"
OTHER = ADAPTER + "/dev_11_22_33_44_55_66"
OWNER = ":1.42"


def adapter(powered=True):
    return {scan_bluetooth.ADAPTER: {"Powered": Variant("b", powered)}}


def device(**extras):
    properties = {"Address": Variant("s", "AA:BB:CC:DD:EE:FF"), "Paired": Variant("b", False)}
    properties.update(extras)
    return {scan_bluetooth.DEVICE: properties}


def added(path=DEVICE, **extras):
    message = Message.new_signal("/", scan_bluetooth.OBJECT_MANAGER, "InterfacesAdded", "oa{sa{sv}}", [path, device(**extras)])
    message.sender = OWNER
    return message


def changed(path=DEVICE, interface=scan_bluetooth.DEVICE, invalidated=None, **extras):
    message = Message.new_signal(path, scan_bluetooth.PROPERTIES, "PropertiesChanged", "sa{sv}as", [interface, extras, invalidated or []])
    message.sender = OWNER
    return message


def removed(path, interface):
    message = Message.new_signal("/", scan_bluetooth.OBJECT_MANAGER, "InterfacesRemoved", "oas", [path, [interface]])
    message.sender = OWNER
    return message


class FakeBus:
    def __init__(self, objects=None, on_start=None, on_snapshot=None, errors=None):
        self.objects = {ADAPTER: adapter()} if objects is None else objects
        self.on_start = on_start
        self.on_snapshot = on_snapshot
        self.errors = errors or {}
        self.handler = None
        self.calls = []
        self.connected = False
        self.session = False
        self.disconnected = asyncio.Event()

    async def connect(self):
        self.connected = True
        return self

    def add_message_handler(self, handler):
        self.handler = handler

    def remove_message_handler(self, handler):
        self.handler = None

    def emit(self, message):
        self.handler(message)

    async def call(self, request):
        self.calls.append(request)
        request.serial = len(self.calls)
        if request.member in self.errors:
            return Message.new_error(request, self.errors[request.member], "simulated failure")
        if request.member == "GetNameOwner":
            return Message.new_method_return(request, "s", [OWNER])
        if request.member == "GetManagedObjects":
            if self.on_snapshot:
                self.on_snapshot(self)
            return Message.new_method_return(request, "a{oa{sa{sv}}}", [self.objects])
        if request.member == "StartDiscovery":
            self.session = True
            if self.on_start:
                self.on_start(self)
        elif request.member == "StopDiscovery":
            self.session = False
        return Message.new_method_return(request)

    def disconnect(self):
        self.connected = False
        self.session = False
        self.disconnected.set()

    async def wait_for_disconnect(self):
        await self.disconnected.wait()

    def members(self):
        return [request.member for request in self.calls]


class CollectorTests(unittest.TestCase):
    def test_cached_devices_and_status_changes_do_not_count_as_discovery(self):
        collector = scan_bluetooth.DeviceCollector(ADAPTER, {DEVICE: device(RSSI=Variant("n", -40))})
        collector.observing = True
        collector.update(DEVICE, {"Name": Variant("s", "Cached name"), "Connected": Variant("b", True)})
        self.assertEqual(collector.records(), [])
        collector.update(DEVICE, {"RSSI": Variant("n", -45)})
        record = collector.records()[0]
        self.assertEqual(record["properties"]["Name"], "Cached name")
        self.assertTrue(record["properties"]["Connected"])

    def test_delayed_name_updates_merge_and_invalidated_fields_become_unknown(self):
        collector = scan_bluetooth.DeviceCollector(ADAPTER, {})
        collector.observing = True
        collector.update(DEVICE, {"RSSI": Variant("n", -60)}, added=True)
        collector.update(DEVICE, {"Name": Variant("s", "Sensor")})
        collector.update(DEVICE, {"RSSI": Variant("n", -42)})
        self.assertEqual(len(collector.records()), 1)
        self.assertEqual(collector.records()[0]["properties"]["RSSI"], -42)
        collector.update(DEVICE, {}, ["RSSI"])
        self.assertIsNone(collector.records()[0]["properties"]["RSSI"])
        self.assertEqual(collector.records()[0]["properties"]["Name"], "Sensor")

    def test_other_adapter_is_ignored_and_removed_observations_are_retained(self):
        collector = scan_bluetooth.DeviceCollector(ADAPTER, {})
        collector.observing = True
        collector.update("/org/bluez/hci1/dev_00", {}, added=True)
        collector.update(DEVICE, {"RSSI": Variant("n", -30)}, added=True)
        collector.remove(DEVICE)
        self.assertEqual(len(collector.records()), 1)
        self.assertEqual(collector.records()[0]["properties"]["RSSI"], -30)

    def test_json_preserves_types_and_encodes_binary_data(self):
        collector = scan_bluetooth.DeviceCollector(ADAPTER, {})
        collector.observing = True
        collector.update(DEVICE, {
            "ManufacturerData": Variant("a{qv}", {76: Variant("ay", b"\x00\xff")}),
            "ServiceData": Variant("a{sv}", {"abcd": Variant("ay", b"\x01\x02")}),
            "Paired": Variant("b", False),
            "RSSI": Variant("n", -50),
            "Extra": Variant("s", "preserved"),
        }, added=True)
        properties = json.loads(scan_bluetooth.format_results(collector.records(), True))[0]["properties"]
        self.assertEqual(properties["ManufacturerData"], {"0x004c": "00ff"})
        self.assertEqual(properties["ServiceData"], {"abcd": "0102"})
        self.assertEqual(properties["Extra"], "preserved")
        self.assertIs(properties["Paired"], False)
        self.assertIsNone(properties["Name"])
        self.assertEqual(properties["RSSI"], -50)

    def test_signal_sort_places_unknown_last(self):
        collector = scan_bluetooth.DeviceCollector(ADAPTER, {})
        collector.observing = True
        for suffix, properties in [("0", {}), ("1", {"RSSI": -80}), ("2", {"RSSI": -30})]:
            collector.update(ADAPTER + "/dev_" + suffix, properties, added=True)
        self.assertEqual([item["properties"]["RSSI"] for item in collector.records()], [-30, -80, None])

    def test_text_escapes_device_control_characters(self):
        collector = scan_bluetooth.DeviceCollector(ADAPTER, {})
        collector.observing = True
        collector.update(DEVICE, {"Name": "bad\x1b[2J\nname"}, added=True)
        output = scan_bluetooth.format_results(collector.records())
        self.assertNotIn("\x1b", output)
        self.assertIn("bad\\x1b[2J\\nname", output)
        self.assertIn("Unknown", output)


class ScanTests(unittest.IsolatedAsyncioTestCase):
    async def scan(self, bus, stop_event=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return await scan_bluetooth.scan(0.005, stop_event, bus_factory=lambda **kwargs: bus)

    async def test_classic_and_ble_events_before_start_reply_are_collected_and_cleanup_runs(self):
        def on_start(bus):
            bus.emit(added(Class=Variant("u", 0x240404), Name=Variant("s", "Headset")))
            bus.emit(added(OTHER, ManufacturerData=Variant("a{qv}", {76: Variant("ay", b"\x01")})))

        bus = FakeBus(on_start=on_start)
        records = await self.scan(bus)
        self.assertEqual(len(records), 2)
        discovery_filter = next(request for request in bus.calls if request.member == "SetDiscoveryFilter")
        self.assertEqual(scan_bluetooth.normalize(discovery_filter.body[0]), {"Transport": "auto", "DuplicateData": True})
        self.assertLess(bus.members().index("AddMatch"), bus.members().index("StartDiscovery"))
        self.assertEqual(bus.members().count("StopDiscovery"), 1)
        self.assertFalse(bus.session)
        self.assertFalse(bus.connected)

    async def test_snapshot_race_preserves_newer_properties_without_counting_old_events(self):
        bus = FakeBus(
            objects={ADAPTER: adapter(), DEVICE: device(Name=Variant("s", "old"))},
            on_snapshot=lambda bus: bus.emit(changed(Name=Variant("s", "new"))),
            on_start=lambda bus: bus.emit(changed(RSSI=Variant("n", -48))),
        )
        records = await self.scan(bus)
        self.assertEqual(records[0]["properties"]["Name"], "new")
        bus = FakeBus(on_snapshot=lambda bus: bus.emit(added(RSSI=Variant("n", -40))))
        self.assertEqual(await self.scan(bus), [])

    async def test_ctrl_c_event_preserves_results_and_releases_session(self):
        stop = asyncio.Event()

        def on_start(bus):
            bus.emit(added(RSSI=Variant("n", -40)))
            stop.set()

        bus = FakeBus(on_start=on_start)
        self.assertEqual(len(await self.scan(bus, stop)), 1)
        self.assertIn("StopDiscovery", bus.members())
        self.assertFalse(bus.connected)

    async def test_callbacks_stream_fresh_records_and_do_not_change_collector_state(self):
        updates, statuses = [], []

        def on_update(record):
            updates.append(record)
            record["properties"]["ManufacturerData"]["0x004c"] = "changed by caller"

        def on_start(bus):
            bus.emit(changed(Connected=Variant("b", True)))
            bus.emit(changed(ManufacturerData=Variant("a{qv}", {76: Variant("ay", b"\x01")})))
            bus.emit(changed(Name=Variant("s", "Sensor")))

        bus = FakeBus(objects={ADAPTER: adapter(), DEVICE: device()}, on_start=on_start)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            records = await scan_bluetooth.scan(0.005, bus_factory=lambda **kwargs: bus, on_update=on_update, on_status=statuses.append)
        self.assertEqual(len(updates), 2)
        self.assertEqual(records[0]["properties"]["ManufacturerData"], {"0x004c": "01"})
        self.assertEqual(records[0]["properties"]["Name"], "Sensor")
        self.assertTrue(statuses[0].startswith("Scanning Classic and BLE"))
        self.assertEqual(output.getvalue(), "")

    async def test_preflight_interrupt_does_not_start_discovery(self):
        stop = asyncio.Event()
        stop.set()
        bus = FakeBus()
        self.assertEqual(await self.scan(bus, stop), [])
        self.assertNotIn("StartDiscovery", bus.members())
        self.assertFalse(bus.connected)

    async def test_missing_and_disabled_adapters_report_clear_errors(self):
        for objects, expected in [({}, "No Bluetooth adapter"), ({ADAPTER: adapter(False)}, "Bluetooth is off")]:
            with self.subTest(objects=objects):
                bus = FakeBus(objects=objects)
                with self.assertRaisesRegex(scan_bluetooth.ScanError, expected):
                    await self.scan(bus)
                self.assertNotIn("StartDiscovery", bus.members())
                self.assertFalse(bus.connected)

    async def test_permission_and_service_errors_are_friendly(self):
        cases = [
            ("StartDiscovery", "org.bluez.Error.NotAuthorized", "access denied"),
            ("AddMatch", scan_bluetooth.DBUS + ".Error.AccessDenied", "access denied"),
            ("GetNameOwner", scan_bluetooth.DBUS + ".Error.NameHasNoOwner", "service is unavailable"),
            ("StartDiscovery", "org.bluez.Error.NotReady", "not ready"),
        ]
        for member, error, expected in cases:
            with self.subTest(error=error):
                bus = FakeBus(errors={member: error})
                with self.assertRaisesRegex(scan_bluetooth.ScanError, expected):
                    await self.scan(bus)
                self.assertFalse(bus.connected)
                self.assertFalse(bus.session)

    async def test_adapter_removed_or_powered_off_releases_discovery(self):
        messages = [
            removed(ADAPTER, scan_bluetooth.ADAPTER),
            changed(ADAPTER, scan_bluetooth.ADAPTER, Powered=Variant("b", False)),
        ]
        for message in messages:
            with self.subTest(member=message.member):
                bus = FakeBus(on_start=lambda bus: bus.emit(message))
                with self.assertRaises(scan_bluetooth.ScanError):
                    await self.scan(bus)
                self.assertIn("StopDiscovery", bus.members())
                self.assertFalse(bus.connected)

    async def test_bus_disconnect_is_reported(self):
        bus = FakeBus(on_start=lambda bus: bus.disconnect())
        with self.assertRaisesRegex(scan_bluetooth.ScanError, "Lost connection"):
            await self.scan(bus)
        self.assertFalse(bus.session)

    async def test_failed_stop_still_disconnects(self):
        bus = FakeBus(errors={"StopDiscovery": "org.bluez.Error.Failed"})
        self.assertEqual(await self.scan(bus), [])
        self.assertFalse(bus.connected)
        self.assertFalse(bus.session)

    async def test_dbus_authentication_failure_has_a_friendly_error(self):
        class DeniedBus(FakeBus):
            async def connect(self):
                raise AuthError("rejected")

        bus = DeniedBus()
        with self.assertRaisesRegex(scan_bluetooth.ScanError, "Access to the system D-Bus was denied"):
            await self.scan(bus)
        self.assertTrue(bus.disconnected.is_set())

    async def test_start_timeout_releases_a_session_even_without_a_reply(self):
        class UnresponsiveBus(FakeBus):
            async def call(self, request):
                if request.member == "StartDiscovery":
                    self.session = True
                    await asyncio.Event().wait()
                return await super().call(request)

        bus = UnresponsiveBus()
        with patch.object(scan_bluetooth, "CALL_TIMEOUT", 0.01):
            with self.assertRaisesRegex(scan_bluetooth.ScanError, "StartDiscovery timed out"):
                await self.scan(bus)
        self.assertFalse(bus.session)
        self.assertFalse(bus.connected)

    async def test_cancelling_the_scan_task_releases_discovery(self):
        ready = asyncio.Event()
        bus = FakeBus(on_start=lambda bus: ready.set())
        with contextlib.redirect_stderr(io.StringIO()):
            task = asyncio.create_task(scan_bluetooth.scan(30, bus_factory=lambda **kwargs: bus))
            await ready.wait()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(bus.connected)
        self.assertFalse(bus.session)

    async def test_service_restart_is_reported_and_cleans_up(self):
        message = Message.new_signal(scan_bluetooth.DBUS_PATH, scan_bluetooth.DBUS, "NameOwnerChanged", "sss", [scan_bluetooth.BLUEZ, OWNER, ":1.43"])
        message.sender = scan_bluetooth.DBUS
        bus = FakeBus(on_start=lambda bus: bus.emit(message))
        with self.assertRaisesRegex(scan_bluetooth.ScanError, "stopped or restarted"):
            await self.scan(bus)
        self.assertFalse(bus.connected)
        self.assertFalse(bus.session)

    async def test_powered_adapter_selection_uses_name_order(self):
        bus = FakeBus(objects={
            "/org/bluez/hci2": adapter(),
            "/org/bluez/hci1": adapter(),
            ADAPTER: adapter(False),
        })
        await self.scan(bus)
        start = next(request for request in bus.calls if request.member == "StartDiscovery")
        self.assertEqual(start.path, "/org/bluez/hci1")

    async def test_real_sigint_handler_emits_partial_json_and_exit_130(self):
        async def interrupting_scan(timeout, stop_event, **kwargs):
            signal.raise_signal(signal.SIGINT)
            await asyncio.wait_for(stop_event.wait(), 1)
            return [{"path": DEVICE, "properties": {"Name": "Sensor"}}]

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(scan_bluetooth, "scan", interrupting_scan), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = await scan_bluetooth.run(SimpleNamespace(timeout=30, json=True))
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(stdout.getvalue())[0]["properties"]["Name"], "Sensor")
        self.assertIn("interrupted", stderr.getvalue())


class ArgumentAndOutputTests(unittest.TestCase):
    def test_invalid_timeouts(self):
        for value in ["0", "-1", "nan", "inf", "-inf", "bad", "1e999"]:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                scan_bluetooth.positive_timeout(value)
        self.assertEqual(scan_bluetooth.positive_timeout("0.5"), 0.5)

    def test_empty_results(self):
        self.assertEqual(scan_bluetooth.format_results([]), "No devices discovered.")
        self.assertEqual(json.loads(scan_bluetooth.format_results([], True)), [])


if __name__ == "__main__":
    unittest.main()
