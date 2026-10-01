#!/usr/bin/env python3
"""Discover nearby Wi-Fi access points through Linux NetworkManager."""

import argparse
import asyncio
import contextlib
import json
import math
import signal
import sys
import time

try:
    from dbus_fast import BusType, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.errors import AuthError, InvalidAddressError
except ImportError:
    sys.exit("Missing dbus-fast. Activate .venv and run: python -m pip install -r requirements.txt")


NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
DEVICE = NM + ".Device"
WIRELESS = DEVICE + ".Wireless"
ACCESS_POINT = NM + ".AccessPoint"
PROPERTIES = "org.freedesktop.DBus.Properties"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
CALL_TIMEOUT = 5
DISPLAY_PROPERTIES = ("BSSID", "SSID", "SSIDHex", "Frequency", "Channel", "Strength", "Security")


class ScanError(Exception):
    """An expected Wi-Fi failure that can be displayed without a traceback."""

    def __init__(self, message, *, name=None):
        super().__init__(message)
        self.name = name


def normalize(value):
    if isinstance(value, Variant):
        if value.signature == "ay":
            return bytes(value.value).hex()
        return normalize(value.value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalize(item) for item in value]
    return value


def channel_for_frequency(frequency):
    """Recognize common 2.4/5/6 GHz channels; never guess for other bands."""
    if not isinstance(frequency, int) or isinstance(frequency, bool):
        return None
    if frequency == 2484:
        return 14
    if 2412 <= frequency <= 2472 and (frequency - 2407) % 5 == 0:
        return (frequency - 2407) // 5
    if frequency % 5 == 0:
        channel = (frequency - 5000) // 5
        if channel in {*range(36, 65, 4), *range(100, 145, 4), *range(149, 178, 4)}:
            return channel
    if frequency == 5935:
        return 2
    if 5955 <= frequency <= 7115 and (frequency - 5955) % 20 == 0:
        return (frequency - 5950) // 5
    return None


def advertised_security(properties):
    """Summarize advertised authentication flags; retain raw flags in results."""
    flags, wpa, rsn = (properties.get(key) for key in ("Flags", "WpaFlags", "RsnFlags"))
    if any(value is None for value in (flags, wpa, rsn)):
        return None
    labels = []
    if wpa:
        labels.append("WPA")
    if rsn & 0x100:
        labels.append("WPA2 Personal")
    if rsn & 0x200:
        labels.append("WPA2/RSN Enterprise")
    if rsn & 0x400:
        labels.append("WPA3 Personal")
    if rsn & 0x800:
        labels.append("Enhanced Open (OWE)")
    if rsn & 0x1000:
        labels.append("OWE transition")
    if rsn & 0x2000:
        labels.append("WPA3 Enterprise Suite-B")
    if rsn and not any(rsn & bit for bit in (0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000)):
        labels.append("RSN (authentication unknown)")
    return ", ".join(labels) if labels else ("WEP/privacy" if flags & 1 else "Open")


def access_point_record(path, raw):
    properties = normalize(raw)
    ssid_hex = properties.get("Ssid")
    properties.update({
        "BSSID": properties.get("HwAddress", "").upper() or None,
        "SSID": bytes.fromhex(ssid_hex).decode("utf-8", errors="replace") if ssid_hex is not None else None,
        "SSIDHex": ssid_hex,
        "Channel": channel_for_frequency(properties.get("Frequency")),
        "Security": advertised_security(properties),
    })
    return {"path": path, "properties": {**dict.fromkeys(DISPLAY_PROPERTIES), **properties}}


def signal_sort_key(record):
    properties = record["properties"]
    strength = properties.get("Strength")
    return (strength is None, -strength if strength is not None else 0, properties.get("BSSID") or record["path"])


def is_fresh(last_seen, started, completed):
    # LastSeen has whole CLOCK_BOOTTIME seconds; LastScan is milliseconds.
    # Up to one second before the request is accepted to account for truncation.
    return (isinstance(last_seen, int) and not isinstance(last_seen, bool)
            and last_seen >= 0 and started - 1 <= last_seen <= completed + 1)


def error_message(name, detail):
    if name in {DBUS + ".Error.ServiceUnknown", DBUS + ".Error.NameHasNoOwner"}:
        return "NetworkManager is unavailable. Check: systemctl status NetworkManager"
    if any(word in name for word in ("PermissionDenied", "NotAuthorized", "AccessDenied", "AuthFailed")):
        return "Wi-Fi scan access denied. Allow this service user the NetworkManager wifi.scan permission."
    if any(word in name for word in ("NotAllowed", "InProgress", "Busy")):
        return f"Wi-Fi scan is busy or unavailable; retry later. {detail}"
    return f"Wi-Fi request failed ({name}): {detail}"


async def dbus_call(bus, destination, path, interface, member, signature="", body=None):
    request = Message(destination=destination, path=path, interface=interface, member=member,
                      signature=signature, body=body if body is not None else [])
    try:
        reply = await asyncio.wait_for(bus.call(request), CALL_TIMEOUT)
    except TimeoutError as exc:
        raise ScanError(f"Wi-Fi request {member} timed out. Check NetworkManager and the adapter.") from exc
    if reply.message_type == MessageType.ERROR:
        detail = reply.body[0] if reply.body else "No details available"
        raise ScanError(error_message(reply.error_name, detail), name=reply.error_name)
    return reply.body


async def scan(timeout, stop_event=None, *, interface=None, bus_factory=MessageBus,
               clock=None, on_status=None):
    """Return a fresh AP snapshot, without altering connectivity.

    timeout bounds the whole operation, including setup and property reads.
    A stop event returns only already collected fresh records; task cancellation
    propagates after cleanup. NetworkManager owns scanning, so there is no
    cancel-scan method: cleanup releases our D-Bus connection/subscriptions.
    clock is injectable CLOCK_BOOTTIME seconds (not wall-clock time).
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive, finite number")
    stop_event = stop_event if stop_event is not None else asyncio.Event()
    if stop_event.is_set():
        return []
    clock = clock or (lambda: time.clock_gettime(time.CLOCK_BOOTTIME))
    bus = None
    owner = None
    device_path = None
    disconnect_waiter = None
    failure = None
    failed = asyncio.Event()
    changed = asyncio.Event()
    records = {}

    def handle_signal(message):
        nonlocal failure
        if message.message_type != MessageType.SIGNAL:
            return
        if message.sender == DBUS and message.interface == DBUS and message.member == "NameOwnerChanged":
            name, previous, current = message.body
            if name == NM and previous == owner and current != owner:
                failure = "NetworkManager stopped or restarted during the Wi-Fi scan. Retry the scan."
                failed.set()
        if message.sender != owner or message.interface != PROPERTIES or message.member != "PropertiesChanged":
            return
        signal_interface, properties, invalidated = message.body
        properties = normalize(properties)
        if message.path == device_path and signal_interface == WIRELESS:
            if "LastScan" in properties or "LastScan" in invalidated:
                changed.set()
        if message.path == device_path and signal_interface == DEVICE and properties.get("Managed") is False:
            failure = "The Wi-Fi adapter is no longer managed by NetworkManager."
            failed.set()
        if message.path == NM_PATH and signal_interface == NM and (properties.get("WirelessEnabled") is False or properties.get("WirelessHardwareEnabled") is False):
            failure = "Wi-Fi was turned off or blocked during the scan."
            failed.set()

    async def get_all(path, property_interface):
        return (await dbus_call(bus, NM, path, PROPERTIES, "GetAll", "s", [property_interface]))[0]

    async def get_last_scan():
        return normalize((await dbus_call(bus, NM, device_path, PROPERTIES, "Get", "ss", [WIRELESS, "LastScan"]))[0])

    async def work():
        nonlocal bus, owner, device_path, disconnect_waiter
        bus = bus_factory(bus_type=BusType.SYSTEM)
        await bus.connect()
        disconnect_waiter = asyncio.create_task(bus.wait_for_disconnect())
        # This watcher turns bus loss into a failure even during LastScan wait.
        disconnect_waiter.add_done_callback(lambda task: failed.set() if not task.cancelled() else None)
        owner = (await dbus_call(bus, DBUS, DBUS_PATH, DBUS, "GetNameOwner", "s", [NM]))[0]
        bus.add_message_handler(handle_signal)
        for rule in (
            "type='signal',sender='org.freedesktop.NetworkManager',interface='org.freedesktop.DBus.Properties'",
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.freedesktop.NetworkManager'",
        ):
            await dbus_call(bus, DBUS, DBUS_PATH, DBUS, "AddMatch", "s", [rule])
        manager = normalize(await get_all(NM_PATH, NM))
        if manager.get("WirelessEnabled") is False or manager.get("WirelessHardwareEnabled") is False:
            raise ScanError("Wi-Fi is off or blocked. Enable Wi-Fi and retry.")
        paths = (await dbus_call(bus, NM, NM_PATH, NM, "GetDevices"))[0]
        candidates = []
        for path in paths:
            props = normalize(await get_all(path, DEVICE))
            if props.get("DeviceType") == 2:
                candidates.append((props.get("Interface", ""), path, props.get("Managed", False)))
        if interface is not None:
            candidates = [item for item in candidates if item[0] == interface]
            if not candidates:
                raise ScanError(f"Wi-Fi interface {interface!r} was not found.")
        if not candidates:
            raise ScanError("No Wi-Fi adapter found. Connect or enable a Wi-Fi adapter.")
        managed = sorted(item for item in candidates if item[2])
        if not managed:
            raise ScanError("No managed Wi-Fi interface is available. Check NetworkManager device settings.")
        name, device_path, _ = managed[0]
        previous_scan = await get_last_scan()
        if on_status:
            on_status(f"Scanning Wi-Fi access points on {name} (timeout {timeout:g} seconds)...")
        started = clock()
        await dbus_call(bus, NM, device_path, WIRELESS, "RequestScan", "a{sv}", [{}])
        # RequestScan acknowledges acceptance only. A completion can race its
        # reply; subscribe first and read LastScan after the acknowledgement.
        changed.set()
        while True:
            await changed.wait()
            changed.clear()
            last_scan = await get_last_scan()
            if last_scan > previous_scan and last_scan >= int(started * 1000):
                break
        completed = last_scan / 1000
        paths = (await dbus_call(bus, NM, device_path, WIRELESS, "GetAllAccessPoints"))[0]
        for path in paths:
            try:
                raw = await get_all(path, ACCESS_POINT)
            except ScanError as exc:
                # An AP disappearing while the snapshot is read is ordinary.
                if exc.name in {DBUS + ".Error.UnknownObject", DBUS + ".Error.UnknownMethod"}:
                    continue
                raise
            properties = normalize(raw)
            if not is_fresh(properties.get("LastSeen"), started, completed):
                continue
            record = access_point_record(path, raw)
            key = record["properties"]["BSSID"] or path
            old = records.get(key)
            if old is None or properties["LastSeen"] >= old["properties"]["LastSeen"]:
                records[key] = record
        return sorted(records.values(), key=signal_sort_key)

    tasks = [asyncio.create_task(work()), asyncio.create_task(stop_event.wait()), asyncio.create_task(failed.wait())]
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if failed.is_set():
            raise ScanError(failure or "Lost connection to the system D-Bus during the Wi-Fi scan.")
        if stop_event.is_set():
            return sorted(records.values(), key=signal_sort_key)
        if tasks[0] in done:
            return tasks[0].result()
        raise ScanError(f"Wi-Fi scan timed out after {timeout:g} seconds without a complete fresh snapshot.")
    except (PermissionError, AuthError) as exc:
        raise ScanError("Access to the system D-Bus was denied. Check this user's permissions.") from exc
    except InvalidAddressError as exc:
        raise ScanError("The system D-Bus address is invalid. Check DBUS_SYSTEM_BUS_ADDRESS.") from exc
    except (OSError, EOFError) as exc:
        raise ScanError(f"Cannot communicate with the system D-Bus: {exc}") from exc
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if bus is not None:
            bus.remove_message_handler(handle_signal)
            bus.disconnect()
        if disconnect_waiter is not None:
            await asyncio.gather(disconnect_waiter, return_exceptions=True)


def display_value(value):
    if value is None:
        return "Unknown"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in str(value))


def format_results(records, json_output=False):
    if json_output:
        return json.dumps(records, indent=2, ensure_ascii=True, allow_nan=False)
    if not records:
        return "No Wi-Fi access points discovered."
    rows = [["SSID", "BSSID", "Signal (%)", "Channel", "Security"]]
    for record in records:
        props = record["properties"]
        ssid = "(Hidden)" if props.get("SSID") == "" else props.get("SSID")
        rows.append([display_value(ssid)[:32], display_value(props.get("BSSID")),
                     display_value(props.get("Strength")), display_value(props.get("Channel")),
                     display_value(props.get("Security"))])
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
    lines.append(f"\n{len(records)} access point(s). MaxBitrate, when present, is advertised capability in Kb/s.")
    for record in records:
        lines.append(f"\n{display_value(record['path'])}")
        lines.extend(f"  {key}: {display_value(value)}" for key, value in sorted(record["properties"].items()))
    return "\n".join(lines)


def positive_timeout(value):
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive, finite number") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive, finite number")
    return duration


async def run(args):
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)
    try:
        records = await scan(args.timeout, stop_event, interface=args.interface,
                             on_status=lambda message: print(message, file=sys.stderr))
        print(format_results(records, args.json))
        if stop_event.is_set():
            print("Scan interrupted; collected fresh results are shown above.", file=sys.stderr)
            return 130
        return 0
    except ScanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=positive_timeout, default=15.0, metavar="SECONDS",
                        help="whole-scan timeout (default: 15)")
    parser.add_argument("--interface", metavar="NAME", help="Wi-Fi interface (default: first managed interface by name)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table and details")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.exit(1, "This scanner requires Linux with NetworkManager.\n")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            sys.stdout.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
