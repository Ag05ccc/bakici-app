#!/usr/bin/env python3
"""Discover Bluetooth Classic and BLE devices through Linux BlueZ."""

import argparse
import asyncio
import contextlib
import copy
import json
import math
import signal
import sys

try:
    from dbus_fast import BusType, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.errors import AuthError, InvalidAddressError
except ImportError:
    sys.exit("Missing dbus-fast. Activate .venv and run: python -m pip install -r requirements.txt")


BLUEZ = "org.bluez"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
CALL_TIMEOUT = 5
DISCOVERY_PROPERTIES = {
    "RSSI", "TxPower", "ManufacturerData", "ServiceData",
    "AdvertisingData", "AdvertisingFlags",
}
DISPLAY_PROPERTIES = (
    "Name", "Address", "AddressType", "RSSI", "TxPower", "UUIDs", "Class",
    "Appearance", "Icon", "ManufacturerData", "ServiceData", "Paired", "Connected",
)


class ScanError(Exception):
    """An expected Bluetooth failure that can be shown without a traceback."""


def normalize(value):
    """Convert D-Bus values to JSON values, preserving byte arrays as hex."""
    if isinstance(value, Variant):
        if value.signature == "ay":
            return bytes(value.value).hex()
        return normalize(value.value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, dict):
        return {
            (f"0x{key:04x}" if isinstance(key, int) else str(key)): normalize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


class DeviceCollector:
    """Keep cached context separate from devices observed during this scan."""

    def __init__(self, adapter_path, objects):
        self.adapter_path = adapter_path
        self.observing = False
        self.cache = {
            path: normalize(interfaces[DEVICE])
            for path, interfaces in objects.items()
            if self.belongs(path) and DEVICE in interfaces
        }
        self.observed = {}

    def belongs(self, path):
        return bool(path and path.startswith(self.adapter_path + "/dev_"))

    def update(self, path, changed, invalidated=(), *, added=False):
        if not self.belongs(path):
            return
        properties = self.cache.setdefault(path, {})
        properties.update(normalize(changed))
        for key in invalidated:
            properties.pop(key, None)
        fresh = added or bool(DISCOVERY_PROPERTIES.intersection(changed))
        if self.observing and (fresh or path in self.observed):
            self.observed[path] = properties.copy()
            return True
        return False

    def remove(self, path):
        # Keep the last observation in this scan even if BlueZ removes its object.
        self.cache.pop(path, None)

    def record(self, path):
        properties = self.observed[path]
        fields = {key: properties.get(key) for key in DISPLAY_PROPERTIES}
        fields.update(properties)
        return {"path": path, "properties": copy.deepcopy(fields)}

    def records(self):
        return sorted((self.record(path) for path in self.observed), key=signal_sort_key)


def signal_sort_key(record):
    properties = record["properties"]
    rssi = properties.get("RSSI")
    return (rssi is None, -rssi if rssi is not None else 0, properties.get("Address") or record["path"])


def select_adapter(objects, adapter=None):
    adapters = sorted(path for path, interfaces in objects.items() if ADAPTER in interfaces)
    if adapter is not None:
        adapters = [path for path in adapters if adapter in (path, path.rsplit("/", 1)[-1])]
        if not adapters:
            raise ScanError(f"Configured Bluetooth adapter {adapter!r} was not found.")
    if not adapters:
        raise ScanError("No Bluetooth adapter found. Connect or enable a Bluetooth adapter.")
    for path in adapters:
        if normalize(objects[path][ADAPTER]).get("Powered"):
            return path
    raise ScanError("Bluetooth is off or blocked. Enable the adapter in Bluetooth settings or with bluetoothctl and retry.")


def error_message(name, detail):
    if name in {"org.bluez.Error.NotAuthorized", "org.bluez.Error.NotPermitted", DBUS + ".Error.AccessDenied", DBUS + ".Error.AuthFailed"}:
        return "Bluetooth access denied. Check this user's system D-Bus/BlueZ permissions."
    if name in {DBUS + ".Error.ServiceUnknown", DBUS + ".Error.NameHasNoOwner"}:
        return "The BlueZ Bluetooth service is unavailable. Check: systemctl status bluetooth"
    if name == "org.bluez.Error.NotReady":
        return "Bluetooth is not ready. Check that the adapter is powered on and not blocked."
    return f"Bluetooth request failed ({name}): {detail}"


async def dbus_call(bus, destination, path, interface, member, signature="", body=None):
    message = Message(
        destination=destination, path=path, interface=interface, member=member,
        signature=signature, body=[] if body is None else body,
    )
    try:
        reply = await asyncio.wait_for(bus.call(message), CALL_TIMEOUT)
    except TimeoutError as exc:
        raise ScanError(f"Bluetooth request {member} timed out. Check the Bluetooth service and adapter.") from exc
    if reply.message_type == MessageType.ERROR:
        detail = reply.body[0] if reply.body else "No details available"
        raise ScanError(error_message(reply.error_name, detail))
    return reply.body


async def scan(timeout, stop_event=None, *, adapter=None, bus_factory=MessageBus, on_update=None, on_status=None):
    """Run one discovery session; a stop event returns collected results early.

    Optional synchronous callbacks receive a device record or status string on
    the scanning event loop. They should finish quickly and never touch a UI.
    The backend itself does not print; CLI/web callers decide how to report status.
    """
    stop_event = stop_event if stop_event is not None else asyncio.Event()
    bus = None
    collector = None
    adapter_path = None
    owner = None
    started = False
    connected = False
    pending = []
    failure = None
    failure_event = asyncio.Event()
    waiters = []
    disconnect_waiter = None

    def fail(message):
        nonlocal failure
        if failure is None:
            failure = message
            failure_event.set()

    def handle_signal(message):
        if message.message_type != MessageType.SIGNAL:
            return
        if message.sender == DBUS and message.interface == DBUS and message.member == "NameOwnerChanged":
            name, old_owner, new_owner = message.body
            if name == BLUEZ and old_owner == owner and new_owner != owner:
                fail("The Bluetooth service stopped or restarted during the scan. Please retry.")
            return
        if message.sender != owner:
            return
        if collector is None:
            pending.append(message)
            return
        if message.interface == OBJECT_MANAGER and message.member == "InterfacesAdded":
            path, interfaces = message.body
            if DEVICE in interfaces:
                updated = collector.update(path, interfaces[DEVICE], added=True)
                if updated and on_update:
                    on_update(collector.record(path))
        elif message.interface == OBJECT_MANAGER and message.member == "InterfacesRemoved":
            path, interfaces = message.body
            if path == adapter_path and ADAPTER in interfaces:
                fail("The Bluetooth adapter was removed during the scan.")
            if DEVICE in interfaces:
                collector.remove(path)
        elif message.interface == PROPERTIES and message.member == "PropertiesChanged":
            interface, changed, invalidated = message.body
            if interface == DEVICE:
                updated = collector.update(message.path, changed, invalidated)
                if updated and on_update:
                    on_update(collector.record(message.path))
            elif interface == ADAPTER and message.path == adapter_path:
                values = normalize(changed)
                if values.get("Powered") is False:
                    fail("Bluetooth was turned off during the scan.")
                elif started and collector.observing and values.get("Discovering") is False:
                    fail("Bluetooth discovery stopped unexpectedly. Please retry.")

    try:
        bus = bus_factory(bus_type=BusType.SYSTEM)
        await asyncio.wait_for(bus.connect(), CALL_TIMEOUT)
        connected = True
        owner = (await dbus_call(bus, DBUS, DBUS_PATH, DBUS, "GetNameOwner", "s", [BLUEZ]))[0]
        bus.add_message_handler(handle_signal)
        for rule in (
            "type='signal',sender='org.bluez'",
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'",
        ):
            await dbus_call(bus, DBUS, DBUS_PATH, DBUS, "AddMatch", "s", [rule])
        objects = (await dbus_call(bus, BLUEZ, "/", OBJECT_MANAGER, "GetManagedObjects"))[0]
        adapter_path = select_adapter(objects, adapter)
        collector = DeviceCollector(adapter_path, objects)
        for message in pending:
            handle_signal(message)
        pending.clear()
        if failure:
            raise ScanError(failure)
        if stop_event.is_set():
            return []
        await dbus_call(bus, BLUEZ, adapter_path, ADAPTER, "SetDiscoveryFilter", "a{sv}", [{
            "Transport": Variant("s", "auto"),
            "DuplicateData": Variant("b", True),
        }])
        if stop_event.is_set():
            return []
        # Enable collection before StartDiscovery: BlueZ can signal before replying.
        collector.observing = True
        await dbus_call(bus, BLUEZ, adapter_path, ADAPTER, "StartDiscovery")
        started = True
        if on_status:
            on_status(f"Scanning Classic and BLE on {adapter_path.rsplit('/', 1)[-1]} for {timeout:g} seconds...")
        waiters = [asyncio.create_task(stop_event.wait()), asyncio.create_task(failure_event.wait())]
        disconnect_waiter = asyncio.create_task(bus.wait_for_disconnect())
        done, _ = await asyncio.wait([*waiters, disconnect_waiter], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if failure:
            raise ScanError(failure)
        if disconnect_waiter in done:
            raise ScanError("Lost connection to the system D-Bus during the scan. Please retry.")
        return collector.records()
    except (PermissionError, AuthError) as exc:
        raise ScanError("Access to the system D-Bus was denied. Check your user permissions.") from exc
    except InvalidAddressError as exc:
        raise ScanError("The system D-Bus address is invalid. Check DBUS_SYSTEM_BUS_ADDRESS.") from exc
    except (OSError, EOFError) as exc:
        raise ScanError(f"Cannot communicate with the system D-Bus: {exc}") from exc
    except TimeoutError as exc:
        raise ScanError("Connecting to the system D-Bus timed out.") from exc
    finally:
        if collector is not None:
            collector.observing = False
        for waiter in waiters:
            waiter.cancel()
        if bus is not None:
            if connected and disconnect_waiter is None:
                disconnect_waiter = asyncio.create_task(bus.wait_for_disconnect())
            try:
                if started and bus.connected:
                    try:
                        await dbus_call(bus, BLUEZ, adapter_path, ADAPTER, "StopDiscovery")
                    except (ScanError, OSError, EOFError) as exc:
                        if on_status:
                            on_status(f"Warning: {exc} Closing the D-Bus connection to release discovery.")
            finally:
                bus.remove_message_handler(handle_signal)
                # Disconnect also releases our filter/session if StartDiscovery timed out.
                bus.disconnect()
        if disconnect_waiter is not None:
            waiters.append(disconnect_waiter)
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)


def display_value(value):
    if value is None:
        return "Unknown"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    # Device-supplied names must not inject terminal control sequences or new rows.
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in str(value))


def format_results(records, json_output=False):
    if json_output:
        return json.dumps(records, indent=2, ensure_ascii=True, allow_nan=False)
    if not records:
        return "No devices discovered."
    rows = [["Name", "Address", "RSSI (dBm)", "Paired", "Connected"]]
    for record in records:
        props = record["properties"]
        name = props.get("Name") or props.get("Alias")
        rows.append([
            display_value(name)[:32], display_value(props.get("Address")),
            display_value(props.get("RSSI")), display_value(props.get("Paired")),
            display_value(props.get("Connected")),
        ])
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
    lines.append(f"\n{len(records)} device(s) observed. Available BlueZ properties (may include cached context):")
    for record in records:
        lines.append(f"\n{display_value(record['path'])}")
        for key, value in sorted(record["properties"].items()):
            lines.append(f"  {key}: {display_value(value)}")
    return "\n".join(lines)


def positive_timeout(value):
    try:
        duration = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive, finite number") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive, finite number")
    return duration


async def run(args):
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    try:
        records = await scan(args.timeout, stop_event, adapter=getattr(args, "adapter", None), on_status=lambda message: print(message, file=sys.stderr))
        print(format_results(records, args.json))
        if stop_event.is_set():
            print("Scan interrupted; collected results are shown above.", file=sys.stderr)
            return 130
        return 0
    except ScanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        loop.remove_signal_handler(signal.SIGINT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=positive_timeout, default=15.0, metavar="SECONDS", help="scan duration (default: 15)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table and details")
    parser.add_argument("--adapter", help="Bluetooth adapter name (for example hci0) or BlueZ object path")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.exit(1, "This scanner requires Linux with BlueZ.\n")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # Keep shell pipelines such as `python scan_bluetooth.py | head` quiet.
        with contextlib.suppress(OSError):
            sys.stdout.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
