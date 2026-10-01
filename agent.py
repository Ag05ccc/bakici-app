#!/usr/bin/env python3
"""Run independent Bluetooth/Wi-Fi scans and send current results to the server.

Only enabled radio modules are imported. HTTP uses one worker per radio plus one
heartbeat worker; busy workers discard new reports instead of queuing old data.
"""

import argparse
import asyncio
import http.client
import importlib
import json
import logging
import math
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


RADIOS = ("bluetooth", "wifi")
UPLOAD_TIMEOUT = 3.0
SETUP_GRACE = 10.0
CLEANUP_GRACE = 6.0
MAX_REPORT_BYTES = 1024 * 1024
LOG = logging.getLogger("radio-agent")


class ConfigError(ValueError):
    """Invalid agent configuration, with no secret values in the message."""


@dataclass(frozen=True)
class AgentConfig:
    node_id: str
    api_key: str = field(repr=False)
    server_url: str
    enabled_radios: tuple = RADIOS
    bluetooth_timeout: float = 15.0
    bluetooth_pause: float = 5.0
    bluetooth_adapter: str | None = None
    wifi_timeout: float = 15.0
    wifi_interval: float = 60.0
    wifi_interface: str | None = None
    heartbeat_interval: float = 20.0


def positive_number(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ConfigError(f"{name} must be a positive, finite number")
    return float(value)


def validate_server_url(value):
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        raise ConfigError("server_url must be an http:// or https:// server address")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("server_url has an invalid address or port") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or port == 0):
        raise ConfigError("server_url needs an http(s) host, optional port, and no credentials, path, query, or fragment")
    return value.rstrip("/")


def parse_config(data):
    if not isinstance(data, dict):
        raise ConfigError("agent configuration must be a JSON object")
    allowed = {"node_id", "api_key", "server_url", "enabled_radios", "bluetooth", "wifi", "heartbeat_interval"}
    if set(data) - allowed:
        raise ConfigError("agent configuration contains unknown fields")
    node_id = data.get("node_id")
    if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", node_id):
        raise ConfigError("node_id must contain 1–64 letters, digits, underscores, or hyphens")
    key = data.get("api_key")
    if not isinstance(key, str) or not 16 <= len(key) <= 256 or any(not 33 <= ord(char) <= 126 for char in key):
        raise ConfigError("api_key must contain 16–256 printable ASCII characters without spaces")
    radios = data.get("enabled_radios", list(RADIOS))
    if (not isinstance(radios, list) or any(not isinstance(radio, str) or radio not in RADIOS for radio in radios)
            or len(set(radios)) != len(radios)):
        raise ConfigError("enabled_radios must be a list of distinct bluetooth/wifi names")
    sections = {}
    for radio, extra in (("bluetooth", {"pause", "adapter"}), ("wifi", {"interval", "interface"})):
        section = data.get(radio, {})
        if not isinstance(section, dict) or set(section) - ({"timeout"} | extra):
            raise ConfigError(f"{radio} configuration contains invalid fields")
        sections[radio] = section
    bt, wifi = sections["bluetooth"], sections["wifi"]
    adapter, interface = bt.get("adapter"), wifi.get("interface")
    if adapter is not None and (not isinstance(adapter, str) or not re.fullmatch(r"hci[0-9]+", adapter)):
        raise ConfigError("bluetooth.adapter must be an adapter name such as hci0, or null")
    if interface is not None and (not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface)):
        raise ConfigError("wifi.interface must be a network interface name such as wlan0, or null")
    return AgentConfig(
        node_id=node_id, api_key=key, server_url=validate_server_url(data.get("server_url")),
        enabled_radios=tuple(radios),
        bluetooth_timeout=positive_number(bt.get("timeout", 15), "bluetooth.timeout"),
        bluetooth_pause=positive_number(bt.get("pause", 5), "bluetooth.pause"),
        bluetooth_adapter=adapter,
        wifi_timeout=positive_number(wifi.get("timeout", 15), "wifi.timeout"),
        wifi_interval=positive_number(wifi.get("interval", 60), "wifi.interval"),
        wifi_interface=interface,
        heartbeat_interval=positive_number(data.get("heartbeat_interval", 20), "heartbeat_interval"),
    )


def load_config(path):
    try:
        with open(path, encoding="utf-8") as source:
            return parse_config(json.load(source))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("cannot read agent configuration; check its path and JSON syntax") from exc


def post_report(config, report):
    """One HTTP request, no redirects/retries and no response-body buffering."""
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError("report exceeds the 1 MiB request limit")
    address = urlsplit(config.server_url)
    connection_type = http.client.HTTPSConnection if address.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(address.hostname, address.port, timeout=UPLOAD_TIMEOUT)
    deadline = time.monotonic() + UPLOAD_TIMEOUT

    def remaining_time():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("report upload timed out")
        connection.sock.settimeout(remaining)

    try:
        connection.connect()
        # OS DNS lookup is not covered by socket timeouts. If resolution took
        # too long, discard before sending an old snapshot after recovery.
        remaining_time()
        connection.request("POST", "/api/reports", body=payload, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {config.api_key}",
        })
        remaining_time()
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise OSError(f"server rejected the report (HTTP {response.status})")
    finally:
        connection.close()


class ReportSender:
    """At most three workers, one request per lane; there is never a queue.

    Workers are daemons because OS hostname resolution may outlast socket
    timeouts. Shutdown waits up to three seconds; it never waits indefinitely
    for DNS or a stuck peer. A blocked lane drops reports until it recovers.
    """

    def __init__(self, config, uploader=post_report):
        self.config = config
        self.uploader = uploader
        self.workers = {}
        self.lock = threading.Lock()
        self.closed = False

    def submit(self, lane, report):
        if lane not in (*RADIOS, "heartbeat"):
            raise ValueError("unknown upload lane")
        with self.lock:
            worker = self.workers.get(lane)
            if self.closed or (worker is not None and worker.is_alive()):
                return False
            worker = threading.Thread(target=self._send, args=(lane, report), daemon=True, name=f"report-{lane}")
            self.workers[lane] = worker
            worker.start()
            return True

    def _send(self, lane, report):
        try:
            self.uploader(self.config, report)
        except Exception:
            # Never log request bodies, raw URLs, credentials, or peer responses.
            LOG.warning("%s upload failed; discarded, the next report will contain new data", lane)

    async def close(self, timeout=UPLOAD_TIMEOUT):
        with self.lock:
            self.closed = True
        deadline = time.monotonic() + timeout
        while any(worker.is_alive() for worker in self.workers.values()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.02, remaining))


async def wait_until_stop(delay, stop_event):
    """Return True on stop, False once a scheduled delay expires."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0, delay))
        return True
    except TimeoutError:
        return stop_event.is_set()


def load_scanner(radio):
    try:
        return importlib.import_module(f"scan_{radio}").scan
    except (ImportError, SystemExit) as exc:
        raise RuntimeError(f"Cannot load {radio} scanner; install requirements.txt in the agent environment") from exc


class Agent:
    def __init__(self, config, *, scanners=None, sender=None, clock=time.monotonic, wait=wait_until_stop):
        self.config = config
        self.scanners = {} if scanners is None else dict(scanners)
        self.sender = sender if sender is not None else ReportSender(config)
        self.clock = clock
        self.wait = wait

    def _report(self, **fields):
        return {"node_id": self.config.node_id, **fields}

    async def _scan(self, radio, timeout, stop_event):
        scanner = self.scanners.get(radio)
        if scanner is None:
            scanner = load_scanner(radio)
            self.scanners[radio] = scanner
        options = ({"adapter": self.config.bluetooth_adapter} if radio == "bluetooth"
                   else {"interface": self.config.wifi_interface})
        # Scan durations are radio windows. Give bounded D-Bus setup additional
        # time, then cancel; the scanner's finally block releases its resources.
        return await asyncio.wait_for(scanner(timeout, stop_event, **options), timeout + SETUP_GRACE)

    async def _radio_loop(self, radio, stop_event):
        timeout = getattr(self.config, f"{radio}_timeout")
        while not stop_event.is_set():
            started = self.clock()
            try:
                observations = await self._scan(radio, timeout, stop_event)
                if not isinstance(observations, list):
                    raise ValueError("scanner returned an invalid observation list")
                report = self._report(kind="scan", radio=radio, status="success", observations=observations)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = "Scan timed out" if isinstance(exc, TimeoutError) else str(exc)
                error = error.replace(self.config.api_key, "[redacted]")
                error = "".join(char if char.isprintable() else " " for char in error)[:500]
                report = self._report(kind="scan", radio=radio, status="error", error=error or "Scan failed")
            if stop_event.is_set():
                break  # Do not publish a partial scan as a completed snapshot.
            self.sender.submit(radio, report)
            delay = (self.config.bluetooth_pause if radio == "bluetooth" else
                     max(0, self.config.wifi_interval - (self.clock() - started)))
            if await self.wait(delay, stop_event):
                break

    async def _heartbeat_loop(self, stop_event):
        while not stop_event.is_set():
            self.sender.submit("heartbeat", self._report(kind="heartbeat", enabled_radios=list(self.config.enabled_radios)))
            if await self.wait(self.config.heartbeat_interval, stop_event):
                break

    async def run(self, stop_event=None):
        stop_event = stop_event if stop_event is not None else asyncio.Event()
        tasks = []
        try:
            if stop_event.is_set():
                return
            tasks = [asyncio.create_task(self._radio_loop(radio, stop_event), name=f"scan-{radio}")
                     for radio in self.config.enabled_radios]
            tasks.append(asyncio.create_task(self._heartbeat_loop(stop_event), name="heartbeat"))
            await stop_event.wait()
        finally:
            stop_event.set()
            # Cooperative stop gives BlueZ time to stop discovery. The backends
            # bound each D-Bus call to five seconds. Cancel stalled scans next.
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=CLEANUP_GRACE)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.sender.close()


async def run(config):
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)
    try:
        await Agent(config).run(stop_event)
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="agent JSON configuration (keep real keys in *.local.json)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = load_config(args.config)
        LOG.info("Starting %s; enabled radios: %s", config.node_id, ", ".join(config.enabled_radios) or "none")
        asyncio.run(run(config))
    except ConfigError as exc:
        parser.exit(2, f"Configuration error: {exc}\n")
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
