# Run one scanner node

The server and agent run in separate processes, including on a Pi hosting both. The agent needs the scanner virtual environment and OS radio services; the server uses only standard-library Python.

```bash
cp config/agent.example.json config/agent.local.json
# Edit node_id, api_key, and server_url to match your server registry.
.venv/bin/python agent.py --config config/agent.local.json
```

Start the receiving server first using the [report protocol instructions](report-protocol.md). The default example points at `http://127.0.0.1:8001`, useful for a one-machine demo. Remote nodes need the server's actual LAN IP or hostname. `0.0.0.0` is not a destination address.

The server owns each node's floor, label, and position. Agents identify themselves by `node_id`, never by IP. Give each node its own key and do not copy the complete server registry to every node.

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled_radios` | `["bluetooth", "wifi"]` | Empty list sends only heartbeats. Disabled radios are not imported. |
| `bluetooth.timeout` | 15 | Discovery duration in seconds |
| `bluetooth.pause` | 5 | Pause after each Bluetooth attempt |
| `bluetooth.adapter` | `null` | First powered adapter; optionally `hci0`, `hci1`, etc. |
| `wifi.timeout` | 15 | Maximum duration of a Wi-Fi scan, including setup |
| `wifi.interval` | 60 | Target start-to-start interval; Wi-Fi scans never overlap |
| `wifi.interface` | `null` | First managed Wi-Fi interface; optionally `wlan0`, etc. |
| `heartbeat_interval` | 20 | Seconds between heartbeat attempts |

Numbers must be positive and finite. Keep intervals compatible with the server's freshness limits (node 60 seconds, Bluetooth 60 seconds, Wi-Fi 150 seconds by default).

Each radio has its own loop. A scanner failure sends a bounded error for that radio and retries at its next scheduled attempt. The other radio and heartbeat keep running. The agent adds a 10-second setup allowance around each scanner timeout before cancelling a stalled scan; individual Bluetooth D-Bus calls have a 5-second timeout.

Uploads use a separate worker for each radio and heartbeat, with at most three active workers and a 3-second HTTP timeout. Scanners continue while uploads run. If a worker is busy, its next report is dropped; failures are also discarded. There is no offline queue, concurrent upload for the same radio, or replay after recovery. Delayed name resolution is checked before transmitting so expired work is discarded. The next accepted upload contains a newly completed scan.

Ctrl+C or SIGTERM stops new attempts, allows up to 6 seconds of cooperative scanner cleanup, then cancels remaining tasks. Scanner cleanup releases its own D-Bus resources. Upload shutdown waits at most 3 seconds; an OS resolver that does not return cannot keep the process alive. Incomplete scans during shutdown are not uploaded as completed snapshots.

Inspect `GET /api/dashboard` for node liveness and per-radio results. An error is different from a successful scan containing no observations. The server may be online even when one radio is unavailable.

Tests without radio hardware or third-party packages:

```bash
python3 -S -W error -m unittest discover -s tests -p 'test_agent.py' -v
```

Real Pi service permissions, scanning while using the same Wi-Fi interface for reports, and power/reboot recovery require the physical pilot described in T7 of the test plan.
