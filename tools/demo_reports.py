#!/usr/bin/env python3
"""Send deterministic development fixtures; requires only Python's standard library."""

import argparse
import json
import math
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def load_nodes(path):
    """Read only the node identities/keys needed by the sample sender."""
    with Path(path).open(encoding="utf-8") as source:
        config = json.load(source)
    nodes = config.get("nodes") if isinstance(config, dict) else None
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Configuration must contain a nonempty nodes list.")
    identities = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Every node must be an object.")
        node_id, key = node.get("id"), node.get("api_key")
        if not isinstance(node_id, str) or not node_id or node_id in identities:
            raise ValueError("Every node must have a unique, nonempty string id.")
        if not isinstance(key, str) or len(key) < 16 or not key.isascii() or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in key
        ):
            raise ValueError("Every node needs an ASCII API key of at least 16 characters without whitespace.")
        identities.add(node_id)
    return nodes


def scan_report(node_id, radio, observations):
    return {"node_id": node_id, "kind": "scan", "radio": radio,
            "status": "success", "observations": observations}


def one_node_steps(node_id):
    """Synthetic locally administered addresses; no real discovery data."""
    return [
        ("Heartbeat: enable Bluetooth and Wi-Fi", {
            "node_id": node_id, "kind": "heartbeat",
            "enabled_radios": ["bluetooth", "wifi"],
        }),
        ("Bluetooth: two observations", scan_report(node_id, "bluetooth", [
            {"path": "/demo/bluetooth/device_01", "properties": {
                "Name": "Demo beacon", "Address": "02:00:00:00:00:01",
                "AddressType": "public", "RSSI": -48,
            }},
            {"path": "/demo/bluetooth/device_02", "properties": {
                "Name": "Demo headphones", "Address": "02:00:00:00:00:02",
                "AddressType": "public", "RSSI": -72,
            }},
        ])),
        ("Wi-Fi: one access point", scan_report(node_id, "wifi", [
            {"path": "/demo/wifi/access_point_01", "properties": {
                "SSID": "Demo network", "BSSID": "02:00:00:00:01:01",
                "Strength": 78, "Frequency": 2412,
            }},
        ])),
        ("Bluetooth: successful empty scan (count becomes 0)",
         scan_report(node_id, "bluetooth", [])),
        ("Wi-Fi: simulated failure (retain previous results as stale)", {
            "node_id": node_id, "kind": "scan", "radio": "wifi",
            "status": "error", "error": "Simulated Wi-Fi scan failure",
        }),
    ]


def building_reports(node_id, index):
    """Repeatable fixtures; index is the node's one-based configuration position."""
    bluetooth = []
    for device in range(index % 3 + 1):
        properties = {"Name": "Demo shared beacon" if device == 0 else f"Demo beacon {index}-{device}",
                      "Address": "02:00:00:00:00:01" if device == 0 else f"02:00:00:00:{index:02X}:{device:02X}",
                      "AddressType": "random" if index == 9 and device == 0 else "public",
                      "RSSI": -40 - index * 3 - device * 8}
        if index == 8 and device == 2:
            properties.pop("Address")
            properties.pop("AddressType")
            properties["Name"] = "Demo observation without address"
        bluetooth.append({"path": f"/demo/{node_id}/bluetooth/device_{device}", "properties": properties})
    wifi = [
        {"path": f"/demo/{node_id}/wifi/access_point_{access_point}", "properties": {
            "BSSID": f"02:00:00:00:01:{access_point + 1:02X}",
            "SSID": "Demo shared network", "Strength": 90 - index * 4 - access_point * 10,
            "Frequency": 2412 if access_point == 0 else 5180,
        }}
        for access_point in range(index % 2 + 1)
    ]
    return [
        {"node_id": node_id, "kind": "heartbeat", "enabled_radios": ["bluetooth", "wifi"]},
        scan_report(node_id, "bluetooth", bluetooth),
        scan_report(node_id, "wifi", wifi),
    ]


def positive_seconds(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Interval must be a positive, finite number.") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Interval must be a positive, finite number.")
    return number


def positive_cycles(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Cycles must be a positive integer.") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("Cycles must be a positive integer.")
    return number


def radio_options(values, known_ids):
    choices = set()
    for value in values:
        parts = value.rsplit(":", 1)
        if len(parts) != 2 or parts[0] not in known_ids or parts[1] not in ("bluetooth", "wifi"):
            raise ValueError("Radio options require a configured node ID followed by :bluetooth or :wifi.")
        choices.add(tuple(parts))
    return choices


def run_building(url, nodes, args, paused, error_radios, empty_radios):
    """Drop failed sends and continue; each next cycle starts with fresh fixtures."""
    cycle = 0
    any_failed = False
    print("Sending synthetic building reports. Ctrl+C stops; restart with changed flags to simulate recovery.", flush=True)
    while args.cycles is None or cycle < args.cycles:
        cycle += 1
        sent, failed = 0, 0
        for index, node in enumerate(nodes, start=1):
            if node["id"] in paused:
                continue
            reports = []
            for report in building_reports(node["id"], index):
                radio = (node["id"], report.get("radio"))
                if radio in empty_radios:
                    report["observations"] = []
                if radio in error_radios:
                    if cycle == 1:
                        reports.append(report)
                    reports.append({"node_id": node["id"], "kind": "scan", "radio": report["radio"],
                                    "status": "error", "error": "Simulated radio scan failure"})
                else:
                    reports.append(report)
            for report in reports:
                try:
                    send_report(url, report, node["api_key"])
                    sent += 1
                except HTTPError as error:
                    print(f"Report rejected: HTTP {error.code}.", file=sys.stderr)
                    error.close()
                    failed += 1
                except (URLError, OSError):
                    print("Cannot send report: server unavailable or request timed out.", file=sys.stderr)
                    failed += 1
        any_failed = any_failed or failed > 0
        print(f"Cycle {cycle}: sent {sent} reports; {len(paused)} nodes paused; {failed} upload errors.", flush=True)
        if args.cycles is None or cycle < args.cycles:
            time.sleep(args.interval)
    return 1 if any_failed else 0


def report_url(server):
    parsed = urlsplit(server)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("Server must be an HTTP(S) base URL without credentials, query, or fragment.")
    # Accessing port validates invalid port strings before making a request.
    parsed.port
    return server.rstrip("/") + "/api/reports"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # A development endpoint should not forward a node's key elsewhere.
        return None


def send_report(url, report, api_key):
    request = Request(url, data=json.dumps(report, allow_nan=False).encode("utf-8"),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + api_key}, method="POST")
    with build_opener(_NoRedirect).open(request, timeout=3) as response:
        return response.status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8001")
    parser.add_argument("--config", default="config/server.local.json")
    parser.add_argument("--scenario", choices=["one-node", "nine-nodes"], default="one-node")
    parser.add_argument("--auto", action="store_true", help="Send all steps without Enter prompts.")
    parser.add_argument("--interval", type=positive_seconds, default=10,
                        help="Seconds between building cycles (default: 10).")
    parser.add_argument("--cycles", type=positive_cycles, help="Stop after this many building cycles (default: run continuously).")
    parser.add_argument("--pause-node", action="append", default=[], metavar="NODE",
                        help="Send no reports for this node; may be repeated.")
    parser.add_argument("--error-radio", action="append", default=[], metavar="NODE:RADIO",
                        help="Seed a result, then report errors for this radio; may be repeated.")
    parser.add_argument("--empty-radio", action="append", default=[], metavar="NODE:RADIO",
                        help="Report successful empty scans for this radio; may be repeated.")
    args = parser.parse_args(argv)
    try:
        nodes = load_nodes(args.config)
        url = report_url(args.server)
        known_ids = {node["id"] for node in nodes}
        paused = set(args.pause_node)
        if not paused <= known_ids:
            raise ValueError("Every paused node must exist in the configuration.")
        error_radios = radio_options(args.error_radio, known_ids)
        empty_radios = radio_options(args.empty_radio, known_ids)
        if error_radios & empty_radios:
            raise ValueError("A radio cannot be both empty and failed.")
        if args.scenario == "one-node" and (paused or error_radios or empty_radios or args.cycles is not None):
            raise ValueError("Node/radio controls and cycles require --scenario nine-nodes.")
    except (OSError, ValueError) as error:
        # JSON decode errors can include file content; avoid printing any config value.
        if isinstance(error, json.JSONDecodeError):
            print("Cannot read configuration: invalid JSON.", file=sys.stderr)
        else:
            print(f"Cannot start demo: {error}", file=sys.stderr)
        return 1

    try:
        if args.scenario == "nine-nodes":
            return run_building(url, nodes, args, paused, error_radios, empty_radios)
        node = nodes[0]
        steps = one_node_steps(node["id"])
        print("Sending synthetic reports. Inspect /api/dashboard after each step.")
        for index, (label, report) in enumerate(steps, start=1):
            status = send_report(url, report, node["api_key"])
            print(f"Step {index}/{len(steps)}: {label} — HTTP {status}", flush=True)
            if not args.auto and sys.stdin.isatty() and index != len(steps):
                input("Inspect the dashboard, then press Enter for the next step: ")
    except HTTPError as error:
        print(f"Report rejected: HTTP {error.code}.", file=sys.stderr)
        error.close()
        return 1
    except (URLError, OSError):
        print("Cannot send report: server unavailable or request timed out.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Demo stopped.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
