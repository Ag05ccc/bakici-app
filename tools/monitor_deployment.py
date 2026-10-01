#!/usr/bin/env python3
"""Record deployment health as JSON Lines after the expected fleet is online and fresh."""

import argparse
from datetime import datetime, timezone
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


RADIOS = ("bluetooth", "wifi")
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def positive_seconds(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Value must be a positive, finite number.") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Value must be a positive, finite number.")
    return number


def positive_integer(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Value must be a positive integer.") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return number


def dashboard_url(server):
    try:
        parsed = urlsplit(server)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError
        parsed.port
    except ValueError:
        raise ValueError("Server must be an HTTP(S) URL without credentials, query, or fragment.") from None
    return server.rstrip("/") + "/api/dashboard"


def fetch_dashboard(url, timeout):
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Dashboard response is too large.")
    return json.loads(body)


def _age(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid age.")
    return value


def summarize_dashboard(dashboard, expected_nodes, node_ids=None):
    """Copy only health fields; never serialize observations or arbitrary error strings."""
    if not isinstance(dashboard, dict) or not isinstance(dashboard.get("nodes"), list):
        raise ValueError("Invalid dashboard.")
    nodes = []
    identities = set()
    errors = []
    fresh = dict.fromkeys(RADIOS, 0)
    disabled = dict.fromkeys(RADIOS, 0)
    selected = set(node_ids) if node_ids else None
    for raw in dashboard["nodes"]:
        if not isinstance(raw, dict):
            raise ValueError("Invalid node.")
        node_id = raw.get("id")
        if (not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", node_id)
                or node_id in identities or raw.get("state") not in ("online", "offline", "waiting")):
            raise ValueError("Invalid node identity or state.")
        identities.add(node_id)
        if selected is not None and node_id not in selected:
            continue
        node_age = _age(raw["age"])
        heartbeat_age = _age(raw["heartbeat_age"])
        if raw["state"] != "waiting" and node_age is None:
            raise ValueError("Missing report age.")
        node = {"id": node_id, "state": raw["state"], "report_age_seconds": node_age,
                "heartbeat_age_seconds": heartbeat_age, "radios": {}}
        if node["state"] != "online":
            errors.append(f"{node_id}:{node['state']}")
        if not isinstance(raw.get("radios"), dict):
            raise ValueError("Missing radios.")
        for radio in RADIOS:
            info = raw["radios"].get(radio)
            if not isinstance(info, dict) or info.get("state") not in ("fresh", "stale", "error", "offline", "waiting", "disabled"):
                raise ValueError("Invalid radio state.")
            radio_age = _age(info["age"])
            if info["state"] == "fresh" and radio_age is None:
                raise ValueError("Missing successful scan age.")
            node["radios"][radio] = {"state": info["state"], "last_success_age_seconds": radio_age}
            if info["state"] == "fresh":
                fresh[radio] += 1
            elif info["state"] == "disabled":
                disabled[radio] += 1
            else:
                errors.append(f"{node_id}:{radio}:{info['state']}")
        nodes.append(node)
    if len(nodes) != expected_nodes:
        errors.append("node_count_mismatch")
    if selected is not None:
        errors.extend(f"{node_id}:missing" for node_id in sorted(selected - identities))
    return {"configured_node_count": len(dashboard["nodes"]), "node_count": len(nodes),
            "online_nodes": sum(node["state"] == "online" for node in nodes),
            "fresh_radios": fresh, "disabled_radios": disabled, "nodes": nodes, "errors": errors}


def parse_process_stat(text, clock_ticks, page_size):
    """Parse Linux /proc/PID/stat, whose parenthesized command may contain spaces or ')'."""
    prefix, separator, tail = text.rpartition(")")
    if (not separator or "(" not in prefix or not prefix.split("(", 1)[0].strip().isdigit()
            or clock_ticks <= 0 or page_size <= 0):
        raise ValueError("Invalid process metrics.")
    fields = tail.split()  # First item is field 3 (state).
    try:
        user_ticks, system_ticks, started, rss_pages = (int(fields[index]) for index in (11, 12, 19, 21))
    except (IndexError, ValueError):
        raise ValueError("Invalid process metrics.") from None
    if min(user_ticks, system_ticks, started, rss_pages) < 0:
        raise ValueError("Invalid process metrics.")
    return {"rss_kib": rss_pages * page_size / 1024,
            "cpu_seconds": (user_ticks + system_ticks) / clock_ticks, "start_ticks": started}


def read_process_metrics(pid):
    text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return parse_process_stat(text, os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE"))


def _write(record):
    print(json.dumps(record, allow_nan=False, separators=(",", ":")), flush=True)


def run_monitor(url, duration, interval, expected_nodes, pid=None, *, fetch=None, clock=None,
                sleep=None, resource_reader=None, write=None, node_ids=None):
    fetch = fetch or fetch_dashboard
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    resource_reader = resource_reader or read_process_metrics
    write = write or _write
    started = clock()
    deadline = started + duration
    summary = {"type": "summary", "duration_requested_seconds": duration, "samples": 0, "failed_samples": 0,
               "request_failures": 0, "invalid_responses": 0, "min_rss_kib": None, "max_rss_kib": None,
               "cpu_seconds_start": None, "cpu_seconds_end": None, "cpu_seconds_used": None,
               "max_report_age_seconds": None, "max_radio_age_seconds": dict.fromkeys(RADIOS),
               "max_disabled_radios": dict.fromkeys(RADIOS, 0), "interrupted": False}
    process_start = None
    try:
        while clock() < deadline:
            sample_started = clock()
            if sample_started >= deadline:
                break
            record = {"type": "sample", "time": datetime.now(timezone.utc).isoformat(),
                      "elapsed_seconds": max(0, sample_started - started), "configured_node_count": None, "node_count": None,
                      "online_nodes": None, "fresh_radios": None, "disabled_radios": None,
                      "nodes": [], "errors": [], "resources": None}
            try:
                payload = fetch(url, min(3, deadline - sample_started))
            except (URLError, OSError, HTTPException) as error:
                if isinstance(error, HTTPError):
                    error.close()
                record["errors"].append("request_failed")
                summary["request_failures"] += 1
            except (ValueError, UnicodeError):
                record["errors"].append("invalid_response")
                summary["invalid_responses"] += 1
            else:
                try:
                    record.update(summarize_dashboard(payload, expected_nodes, node_ids))
                except (ValueError, TypeError, KeyError, OverflowError):
                    record["errors"].append("invalid_response")
                    summary["invalid_responses"] += 1
            if pid is not None:
                try:
                    metrics = resource_reader(pid)
                    if process_start is not None and process_start != metrics["start_ticks"]:
                        raise ValueError("Process was replaced.")
                    process_start = metrics["start_ticks"]
                    record["resources"] = {key: metrics[key] for key in ("rss_kib", "cpu_seconds")}
                    rss = metrics["rss_kib"]
                    summary["min_rss_kib"] = rss if summary["min_rss_kib"] is None else min(summary["min_rss_kib"], rss)
                    summary["max_rss_kib"] = rss if summary["max_rss_kib"] is None else max(summary["max_rss_kib"], rss)
                    if summary["cpu_seconds_start"] is None:
                        summary["cpu_seconds_start"] = metrics["cpu_seconds"]
                    summary["cpu_seconds_end"] = metrics["cpu_seconds"]
                    summary["cpu_seconds_used"] = summary["cpu_seconds_end"] - summary["cpu_seconds_start"]
                except (OSError, ValueError, KeyError, TypeError):
                    record["errors"].append("process_metrics_unavailable")
            for node in record["nodes"]:
                age = node["report_age_seconds"]
                if age is not None:
                    old = summary["max_report_age_seconds"]
                    summary["max_report_age_seconds"] = age if old is None else max(old, age)
                for radio, state in node["radios"].items():
                    radio_age = state["last_success_age_seconds"]
                    if radio_age is not None:
                        old = summary["max_radio_age_seconds"][radio]
                        summary["max_radio_age_seconds"][radio] = radio_age if old is None else max(old, radio_age)
            if record["disabled_radios"] is not None:
                for radio in RADIOS:
                    summary["max_disabled_radios"][radio] = max(summary["max_disabled_radios"][radio], record["disabled_radios"][radio])
            summary["samples"] += 1
            summary["failed_samples"] += bool(record["errors"])
            write(record)
            remaining = deadline - clock()
            if remaining > 0:
                sleep(min(remaining, max(0, interval - (clock() - sample_started))))
    except KeyboardInterrupt:
        summary["interrupted"] = True
    summary["elapsed_seconds"] = max(0, clock() - started)
    summary["checks_passed"] = bool(summary["samples"] and not summary["failed_samples"] and not summary["interrupted"])
    write(summary)
    return 130 if summary["interrupted"] else 0 if summary["checks_passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8001")
    parser.add_argument("--duration", type=positive_seconds, default=3600, help="Seconds to monitor (default: 3600).")
    parser.add_argument("--interval", type=positive_seconds, default=10, help="Seconds between polls (default: 10).")
    parser.add_argument("--expected-nodes", type=positive_integer, default=9)
    parser.add_argument("--node", action="append", default=[], metavar="NODE",
                        help="Monitor only selected node IDs; may be repeated. Missing IDs fail the check.")
    parser.add_argument("--pid", type=positive_integer, help="Local Linux server PID for CPU/RSS; omit for remote servers.")
    args = parser.parse_args(argv)
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", node_id) for node_id in args.node):
        parser.error("Selected node IDs must contain only letters, digits, underscores or hyphens.")
    try:
        url = dashboard_url(args.server)
    except ValueError as error:
        parser.error(str(error))
    return run_monitor(url, args.duration, args.interval, args.expected_nodes, args.pid, node_ids=args.node)


if __name__ == "__main__":
    raise SystemExit(main())
