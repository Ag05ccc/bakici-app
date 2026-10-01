"""Thread-safe, in-memory node observations; no radio or third-party imports."""

import copy
import hmac
import json
import math
import re
import threading
import time


RADIOS = ("bluetooth", "wifi")
DEFAULT_FRESHNESS = {"node": 60, "bluetooth": 60, "wifi": 150}
MAX_REPORT_BYTES = 1024 * 1024
MAX_OBSERVATIONS = 1024
MAX_PROPERTIES = 256
MAX_STRING_LENGTH = 16384
MAX_DEPTH = 8
MAX_JSON_VALUES = 32768


class ReportError(ValueError):
    """A rejected report and the appropriate HTTP status (without credentials)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_config(config):
    """Validate and copy only the supported configuration fields."""
    if not isinstance(config, dict):
        raise ValueError("Server configuration must be a JSON object.")
    nodes = config.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 128:
        raise ValueError("Server configuration requires between 1 and 128 nodes.")
    result = {"nodes": [], "freshness": dict(DEFAULT_FRESHNESS)}
    seen_ids, seen_keys = set(), set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Each configured node must be an object.")
        node_id, key = node.get("id"), node.get("api_key")
        if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", node_id):
            raise ValueError("Each node requires an id of 1–64 letters, digits, underscores or hyphens.")
        if node_id in seen_ids:
            raise ValueError("Configured node IDs must be unique.")
        if not _text(key, 256) or len(key) < 16 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError("Each node api_key requires 16–256 printable ASCII characters without whitespace.")
        if key in seen_keys:
            raise ValueError("Configured node API keys must be unique.")
        if not _text(node.get("label"), 128) or not _text(node.get("location"), 256):
            raise ValueError("Each node requires a label and location.")
        if type(node.get("floor")) is not int or node["floor"] not in (1, 2, 3):
            raise ValueError("Each node floor must be the integer 1, 2 or 3.")
        if any(not _number(node.get(axis)) or not 0 <= node[axis] <= 100 for axis in ("x", "y")):
            raise ValueError("Each node x/y coordinate must be a finite percentage from 0 to 100.")
        seen_ids.add(node_id)
        seen_keys.add(key)
        result["nodes"].append({field: node[field] for field in ("id", "label", "floor", "location", "x", "y", "api_key")})
    freshness = config.get("freshness", {})
    if not isinstance(freshness, dict) or set(freshness) - set(DEFAULT_FRESHNESS):
        raise ValueError("Freshness settings support only node, bluetooth and wifi.")
    for name, value in freshness.items():
        if not _number(value) or value <= 0:
            raise ValueError("Freshness limits must be positive finite seconds.")
        result["freshness"][name] = value
    return result


def load_config(path):
    """Read server JSON with useful startup errors, never echoing its contents."""
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except OSError as exc:
        raise ValueError(f"Cannot read server configuration: {exc.strerror}.") from exc
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Server configuration must contain valid UTF-8 JSON.") from exc
    return validate_config(config)


def _validate_json(value):
    """Check depth, size, types and finite numbers before copying input data."""
    remaining = MAX_JSON_VALUES

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > MAX_DEPTH:
            raise ReportError("Report exceeds the allowed JSON complexity.")
        if item is None or type(item) is bool:
            return
        if type(item) in (int, float):
            try:
                finite = math.isfinite(item)
            except OverflowError:
                finite = False
            if not finite:
                raise ReportError("Report numbers must be finite.")
            return
        if isinstance(item, str):
            if len(item) > MAX_STRING_LENGTH:
                raise ReportError("Report string exceeds the allowed length.")
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for name, child in item.items():
                if not isinstance(name, str) or len(name) > 256:
                    raise ReportError("Report object keys must be short strings.")
                visit(child, depth + 1)
            return
        raise ReportError("Report contains an unsupported JSON value.")

    visit(value, 0)
    try:
        size = len(json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ReportError("Report must contain valid JSON values.") from exc
    if size > MAX_REPORT_BYTES:
        raise ReportError("Report exceeds the 1 MiB size limit.")


def _validate_report(report):
    _validate_json(report)
    kind = report.get("kind")
    if kind == "heartbeat":
        if set(report) != {"node_id", "kind", "enabled_radios"}:
            raise ReportError("Heartbeat requires only node_id, kind and enabled_radios.")
        radios = report["enabled_radios"]
        if not isinstance(radios, list) or any(radio not in RADIOS for radio in radios) or len(set(radios)) != len(radios):
            raise ReportError("Enabled radios must be a unique list of bluetooth and/or wifi.")
    elif kind == "scan":
        if report.get("radio") not in RADIOS:
            raise ReportError("Scan radio must be bluetooth or wifi.")
        status = report.get("status")
        common = {"node_id", "kind", "radio", "status"}
        if status == "success":
            if set(report) != common | {"observations"}:
                raise ReportError("Successful scan requires observations and no error field.")
            observations = report["observations"]
            if not isinstance(observations, list) or len(observations) > MAX_OBSERVATIONS:
                raise ReportError(f"Observations must be a list of at most {MAX_OBSERVATIONS} records.")
            paths = set()
            for record in observations:
                if not isinstance(record, dict) or set(record) != {"path", "properties"}:
                    raise ReportError("Each observation requires only path and properties.")
                if not _text(record["path"], 1024) or record["path"] in paths:
                    raise ReportError("Observation paths must be nonempty, short and unique within a scan.")
                if not isinstance(record["properties"], dict) or len(record["properties"]) > MAX_PROPERTIES:
                    raise ReportError(f"Observation properties must be an object of at most {MAX_PROPERTIES} fields.")
                paths.add(record["path"])
        elif status == "error":
            if set(report) != common | {"error"} or not _text(report.get("error"), 500):
                raise ReportError("Failed scan requires a nonempty error of at most 500 characters.")
        else:
            raise ReportError("Scan status must be success or error.")
    else:
        raise ReportError("Report kind must be heartbeat or scan.")


class Registry:
    """Store the latest successful scan and latest attempt for each node/radio."""

    def __init__(self, config, *, clock=time.monotonic):
        validated = validate_config(config)
        self._clock = clock
        self._freshness = validated["freshness"]
        self._nodes = {node["id"]: node for node in validated["nodes"]}
        self._lock = threading.Lock()
        self._states = {
            node_id: {
                "received": None, "heartbeat": None, "ip": None, "enabled": None,
                "radios": {radio: {"received": None, "error": None, "observations": [], "invalidated": False} for radio in RADIOS},
            }
            for node_id in self._nodes
        }

    def accept(self, report, token, source_ip):
        """Authenticate and validate the entire report before replacing any state."""
        if not isinstance(report, dict) or not isinstance(report.get("node_id"), str):
            raise ReportError("Report requires a node_id string.")
        if not isinstance(token, str) or not token:
            raise ReportError("A node authorization token is required.", status=401)
        node_id = report["node_id"]
        configured = self._nodes.get(node_id)
        if configured is None or not token.isascii() or not hmac.compare_digest(configured["api_key"], token):
            raise ReportError("Node authentication failed.", status=403)
        _validate_report(report)
        if not isinstance(source_ip, str) or len(source_ip) > 128:
            raise ReportError("Invalid source IP.")
        # The caller and snapshots cannot mutate the registry's stored records.
        clean = copy.deepcopy(report)
        with self._lock:
            now = self._clock()
            state = self._states[node_id]
            state["received"], state["ip"] = now, source_ip
            if clean["kind"] == "heartbeat":
                state["heartbeat"] = now
                state["enabled"] = clean["enabled_radios"]
                for name, radio in state["radios"].items():
                    if name not in state["enabled"]:
                        radio["invalidated"] = True
            else:
                radio = state["radios"][clean["radio"]]
                if clean["status"] == "success":
                    disabled = state["enabled"] is not None and clean["radio"] not in state["enabled"]
                    radio.update(received=now, error=None, observations=clean["observations"], invalidated=disabled)
                else:
                    radio["error"] = clean["error"]

    def snapshot(self):
        """Return a detached public view, computing ages from server monotonic time."""
        with self._lock:
            now = self._clock()
            nodes = []
            for node_id, config in self._nodes.items():
                stored = self._states[node_id]

                def age(received):
                    return None if received is None else max(0, now - received)

                node_age = age(stored["received"])
                node_state = "waiting" if node_age is None else "offline" if node_age >= self._freshness["node"] else "online"
                node = {field: config[field] for field in ("id", "label", "floor", "location", "x", "y")}
                node.update(ip=stored["ip"], state=node_state, age=node_age, heartbeat_age=age(stored["heartbeat"]), radios={})
                for name, radio in stored["radios"].items():
                    radio_age = age(radio["received"])
                    if stored["enabled"] is not None and name not in stored["enabled"]:
                        radio_state = "disabled"
                    elif node_state == "offline":
                        radio_state = "offline"
                    elif radio["error"] is not None:
                        radio_state = "error"
                    elif radio_age is None:
                        radio_state = "waiting"
                    elif radio["invalidated"] or radio_age >= self._freshness[name]:
                        radio_state = "stale"
                    else:
                        radio_state = "fresh"
                    node["radios"][name] = {
                        "state": radio_state,
                        "count": len(radio["observations"]) if radio_state == "fresh" else None,
                        "age": radio_age,
                        "error": radio["error"],
                        "observations": copy.deepcopy(radio["observations"]),
                    }
                nodes.append(node)
        return {"nodes": nodes, "all_observations": {radio: _group(nodes, radio) for radio in RADIOS}}


def _group(nodes, radio):
    groups = {}
    for node in nodes:
        results = node["radios"][radio]
        if results["state"] != "fresh":
            continue
        for record in results["observations"]:
            props = record["properties"]
            address = props.get("Address") if radio == "bluetooth" else props.get("BSSID", props.get("HwAddress"))
            address_type = props.get("AddressType") if radio == "bluetooth" else None
            if isinstance(address, str) and address.strip():
                identity = [address.strip().lower()]
                if radio == "bluetooth":
                    identity.append(address_type.lower() if isinstance(address_type, str) else None)
            else:
                identity = ["node-local", node["id"], record["path"]]
            # JSON encoding makes otherwise ambiguous addresses/paths collision-free.
            group_id = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
            group = groups.setdefault(group_id, {"id": group_id, "observations": []})
            group["observations"].append({"node_id": node["id"], **copy.deepcopy(record)})
    return [groups[key] for key in sorted(groups)]
