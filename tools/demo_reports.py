#!/usr/bin/env python3
"""Send deterministic development fixtures; requires only Python's standard library."""

import argparse
import json
from pathlib import Path
import sys
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
    parser.add_argument("--scenario", choices=["one-node"], default="one-node")
    parser.add_argument("--auto", action="store_true", help="Send all steps without Enter prompts.")
    args = parser.parse_args(argv)
    try:
        node = load_nodes(args.config)[0]
        url = report_url(args.server)
    except (OSError, ValueError) as error:
        # JSON decode errors can include file content; avoid printing any config value.
        if isinstance(error, json.JSONDecodeError):
            print("Cannot read configuration: invalid JSON.", file=sys.stderr)
        else:
            print(f"Cannot start demo: {error}", file=sys.stderr)
        return 1

    steps = one_node_steps(node["id"])
    print("Sending synthetic reports. Inspect /api/dashboard after each step.")
    try:
        for index, (label, report) in enumerate(steps, start=1):
            status = send_report(url, report, node["api_key"])
            print(f"Step {index}/{len(steps)}: {label} — HTTP {status}", flush=True)
            if not args.auto and sys.stdin.isatty() and index != len(steps):
                input("Inspect the dashboard, then press Enter for the next step: ")
    except HTTPError as error:
        print(f"Report rejected: HTTP {error.code}.", file=sys.stderr)
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
