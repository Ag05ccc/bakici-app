#!/usr/bin/env python3
"""Create a private set of matching server/node configs, without overwriting files."""

import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import parse_config, validate_server_url
from registry import load_config, validate_config


def generate_configs(template, output, server_url):
    """Return generated paths; configuration secrets are never written to stdout."""
    server_url = validate_server_url(server_url)
    server = load_config(template)
    try:
        agent_template = json.loads((ROOT / "config" / "agent.example.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("Cannot read the agent example configuration.") from exc
    documents = {}
    keys = set()
    for node in server["nodes"]:
        key = secrets.token_urlsafe(32)
        while key in keys:
            key = secrets.token_urlsafe(32)
        keys.add(key)
        node["api_key"] = key
        config = copy.deepcopy(agent_template)
        config.update(node_id=node["id"], api_key=key, server_url=server_url)
        parse_config(config)
        documents[f"{node['id']}.agent.local.json"] = config
    validate_config(server)
    documents["server.local.json"] = server

    output = Path(output)
    try:
        output.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Output path already exists; choose a new directory. Existing keys are never overwritten.") from exc
    except OSError as exc:
        raise ValueError("Cannot create output directory; check its parent directory and permissions.") from exc
    try:
        output.chmod(0o700)
        for name, config in documents.items():
            path = output / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(config, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
    except BaseException:
        shutil.rmtree(output)
        raise
    return [output / name for name in documents]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", required=True, help="server LAN address, e.g. http://192.168.1.50:8001")
    parser.add_argument("--template", type=Path, default=ROOT / "config" / "server.example.json", help="server layout JSON template")
    parser.add_argument("--output", type=Path, default=Path("deployment.local"), help="new private output directory (default: deployment.local)")
    args = parser.parse_args()
    try:
        paths = generate_configs(args.template, args.output, args.server_url)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Configuration generation failed: {exc}\n")
    print(f"Created {len(paths)} private configuration files in {args.output}. No keys were printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
