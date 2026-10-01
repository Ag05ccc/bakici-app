# Building Radio Scanner — System Plan

Status: implemented software architecture on `development/building-radio-system`; physical Pi pilot and rollout remain unverified. See [development-progress.md](development-progress.md) for observed results and outstanding hardware checks.

## 1. Goal and first-version defaults

Deploy nine Raspberry Pi scanner nodes across three floors, with three nodes on each floor. A central server receives observations and serves a browser dashboard showing the building, Pi positions, per-Pi counts, and selectable results.

First-version assumptions:

- One of the nine Pis also hosts the server. A separate server Pi can use exactly the same design.
- All nodes can reach the server on a private building network.
- Show recent observations, with no database or persistent detection history.
- Bluetooth means nearby Classic and BLE discovery, preserving the existing scanner's capabilities.
- **Wi-Fi means nearby networks/access points, not an inventory of phones or laptops connected to a network.** This is the initial interpretation of “check the Wi-Fi network.” LAN-client inventory would be a separate feature.
- Show the configured locations of the Pis. Do not infer exact device positions, distances, or floors from received signals.

The server and agents operate independently. One scanner failing must not prevent another scanner from reporting or the dashboard from loading.

## 2. Module structure

**Use separate `scan_bluetooth.py` and `scan_wifi.py` modules.** Use underscores so the files can be imported normally in Python. The Bluetooth CLI and backend live directly in `scan_bluetooth.py`; the old `scanner.py` entry point has been removed.

| Module | Responsibility |
| --- | --- |
| `scan_bluetooth.py` | BlueZ discovery, Bluetooth CLI, device properties, adapter cleanup |
| `scan_wifi.py` | NetworkManager access-point discovery and Wi-Fi properties |
| `agent.py` | Node configuration, independent radio schedules, reporting, heartbeat, shutdown |
| `registry.py` | Server-side node registry, latest results, health, freshness, and aggregation |
| `web_app.py` | HTTP report receiver, dashboard API, and static-file serving |
| `static/` | Plain HTML/CSS/JavaScript and a small SVG building background |

Give both scanner modules the same small calling convention: an asynchronous scan with a timeout and stop event returns a list of observation records. Keep radio-specific properties inside each record. The agent converts results and exceptions into a common report envelope. Preserve the current Bluetooth callback interface for existing callers.

Scanners do not upload data or know about building locations. The agent does not render pages. The registry does not access Bluetooth or Wi-Fi hardware.

Refactor the current server's unconditional `scan_bluetooth` import: server-only operation must not require BlueZ, NetworkManager, or `dbus-fast`. Run the local agent as a separate process even when the server Pi is also a scanner node.

## 3. Dependencies and radio behaviour

Keep Python 3.11+, the existing `dbus-fast` package, and OS-provided services. Use Python standard-library networking, JSON, timing, and service coordination. The frontend keeps its current plain-browser approach.

### Bluetooth

- Use the existing BlueZ implementation in `scan_bluetooth.py`, including its CLI options and output formats.
- Retain Classic/BLE discovery, fresh-observation filtering, available properties, and clean cancellation.
- Default: scan for 15 seconds, report once, pause for 5 seconds, then repeat.
- Display Bluetooth signal as RSSI in dBm when available.

### Wi-Fi

- Use NetworkManager's D-Bus API through the same `dbus-fast` dependency. Raspberry Pi OS uses NetworkManager by default from Bookworm onwards. [Raspberry Pi networking documentation](https://www.raspberrypi.com/documentation/computers/configuration.html#find-networks)
- Use the configured wireless interface, or the first managed Wi-Fi interface if none is configured. A missing service, adapter, or permission becomes a Wi-Fi error; Bluetooth continues.
- Request a scan, wait for the `LastScan` completion update, then fetch access points. Subscribe before requesting; accepting the request does not mean the scan has finished. [NetworkManager wireless API](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.Device.Wireless.html)
- Filter cached access points using `LastSeen` against the scan window. `LastScan` uses boot-time milliseconds and `LastSeen` uses boot-time seconds; convert units and allow one second for timestamp precision. Unknown or older sightings must not be presented as new discoveries. [Access-point API](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.AccessPoint.html)
- Default: start a scan every 60 seconds, with a 15-second completion timeout. Busy, denied, or timed-out scans report an error and retry at the next scheduled attempt. No overlapping Wi-Fi scans.
- Collect BSSID, SSID, frequency, signal quality, advertised security, and other available properties. Derive channel only for recognized frequency mappings; otherwise show Unknown. Preserve raw SSID bytes for hidden or non-UTF-8 names.
- Wi-Fi signal quality from NetworkManager is a percentage, not dBm. Advertised maximum bitrate is not measured throughput. [Access-point property definitions](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.AccessPoint.html)

Use a separate bounded asynchronous loop for each radio in the agent. Catch failures per loop and let the other radio continue. Each scanner owns its connection and cleanup. A hung scan is cancelled at its deadline instead of blocking the reporting loop indefinitely.

Keep NetworkManager in control of the Wi-Fi connection. Discovery does not disconnect the uplink, switch networks, or enable monitor mode. Scanning on the interface carrying reports can delay connectivity, so validate this on the Pi and tolerate upload failures. Prefer Ethernet where already available; do not require a second adapter initially.

Verify scan permissions under the actual service user. If headless Wi-Fi scans need additional authorization, document a narrowly scoped scan permission; do not solve this by running the entire server as root.

## 4. Node identity, IP addresses, and locations

Use permanent node IDs rather than IP addresses as identity. For example:

| Floor | Node IDs | Initial diagram positions |
| --- | --- | --- |
| 1 | `pi-01`, `pi-02`, `pi-03` | Left, centre, right |
| 2 | `pi-04`, `pi-05`, `pi-06` | Left, centre, right |
| 3 | `pi-07`, `pi-08`, `pi-09` | Left, centre, right |

Actual room names and positions are deployment configuration, not code changes.

The server has one JSON registry containing each node's ID, label, floor, location label, drawing coordinates, and private API key. Coordinates are percentages within the floor panel so the drawing scales with the screen. API keys never appear in dashboard responses. The agent's heartbeat declares which radios are enabled; the server does not maintain a second copy of that setting.

Each agent has a small JSON configuration: node ID, matching API key, server URL, enabled radios, and optional interface/adapter selection. Default timing values may be overridden there, with server freshness limits kept consistent.

Reserve a stable server IP through router DHCP configuration. Other nodes may use DHCP; reservations are optional for maintenance. The server records the source IP of reports for diagnostics. An IP change does not change node identity or move its icon.

Use configurable port 8001 initially, avoiding the existing port-8000 conflict on the development PC. Each node knows the server URL; the server does not need to connect into each node. A colocated agent can report through loopback.

## 5. Reporting, freshness, and recovery

Agents start automatically on boot. Send one fresh report after each radio scan, plus a small heartbeat every 20 seconds. Use bounded HTTP uploads outside scanner callbacks, with a 3-second network timeout. Network work must not block the radio event loop.

Use two endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/reports` | Authenticated node heartbeat or one completed radio result |
| `GET /api/dashboard` | Public layout, node health, per-radio ages/counts, and current observations |

A scan report contains node ID, report kind, radio (`bluetooth` or `wifi`), success/error status, observations on success, and a short error on failure. A heartbeat contains node identity and enabled-radio status, but no repeated observations. Send the per-node API key in the authorization header.

The registry is keyed by node ID and radio. Validate node identity, radio, payload structure, and a bounded request size (initially 1 MiB). Reject malformed or oversized reports without replacing good results. Keep private-network deployment as the initial scope; API keys are not a substitute for encrypted transport on an untrusted network.

Rules:

- A successful report replaces only that node's result set for that radio.
- A successful empty scan means zero observations for that radio.
- An error keeps its previous successful results available, explicitly marked stale and excluded from current totals.
- A Bluetooth report never refreshes Wi-Fi results, or vice versa. Heartbeats never refresh either result set.
- Use server receipt times and monotonic age calculations; do not require synchronized Pi clocks.
- Failed uploads are discarded. Do not build an offline queue or replay old scans after recovery. The next successful upload contains a newly completed scan.
- Keep only the latest result per node/radio in memory. Persist configuration, not observation history.

Initial health limits:

| Condition | Interpretation |
| --- | --- |
| Node has never reported | Waiting for first report |
| No heartbeat or scan report for 60 seconds | Node offline |
| Latest radio attempt reported an error | That radio has a scanner error |
| No new Bluetooth result for 60 seconds | Bluetooth results stale |
| No new Wi-Fi result for 150 seconds | Wi-Fi results stale |
| Radio explicitly disabled | Disabled, not failed |

Track node connectivity separately from radio health. For example, a Pi may be online with working Bluetooth and failed Wi-Fi. Stale/unavailable counts appear as `—`, not zero.

| Failure | System response |
| --- | --- |
| One radio fails | Report that radio's error; the other radio and heartbeat continue |
| Agent process crashes | Its service restarts; other Pis and the GUI continue |
| Pi loses power | Its marker becomes offline until reports resume |
| Wi-Fi uplink drops | Scans continue locally; failed uploads are discarded |
| Server is unreachable | All agents keep scanning; only fresh future scans are uploaded |
| Server restarts | Reload layout; mark nodes waiting and rebuild state from new reports |
| Browser disconnects | Show server-disconnected state and label the last view outdated |

Use separate agent and server `systemd` units, enabled at boot with restart on failure and a short restart delay. An intentional service stop must remain stopped. Keep logs bounded using the existing system journal. [systemd service behaviour](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)

The server Pi is still a single point of failure for dashboard availability. Separate processes isolate a scanner crash, not a power failure of their shared Pi. Redundant servers and recovery of missed observations are later features.

## 6. Building dashboard

Create a small SVG background with three stacked floor panels, Floor 1 at the bottom. Place three clickable Pi markers on each floor from the server's location registry. Begin with a schematic building; an accurate floor-plan image can replace it later.

Add a **Bluetooth / Wi-Fi selector**:

- In Bluetooth mode, marker `pi-04 (3)` means three Bluetooth observations in that Pi's latest current scan.
- In Wi-Fi mode, it means three Wi-Fi access points (BSSIDs), not three client devices or necessarily three network names.
- Marker colour and a text label reflect node availability and the selected radio's health.
- Clicking a Pi shows its floor, location, IP, heartbeat age, both radio health states, and results for the selected radio.
- Clicking an observation shows all available properties. Preserve JSON export.
- Show stale results only with a clear stale label and their age.

An All observations view lists every reporting module for a matching observation. Bluetooth grouping uses reported address and address type; Wi-Fi grouping uses BSSID, never SSID. Missing Bluetooth addresses stay node-local. Keep each module's signal measurement separate. Do not merge Bluetooth and Wi-Fi identities or sum their counts into a physical-device total. Randomized addresses and multi-BSSID access points make such a total unreliable.

Refresh the dashboard every 2 seconds, more slowly in a hidden browser tab. Keep the current HTML/CSS/JavaScript approach and use text-safe rendering for device names and SSIDs. The building dashboard is observational: agents scan automatically; fleet-wide Start/Stop commands are deferred. Preserve the existing standalone Bluetooth CLI and local GUI mode during migration.

## 7. Implementation order and acceptance checks

1. **Name the Bluetooth module (completed):** use `scan_bluetooth.py` directly for the CLI and imports; update callers and verify Bluetooth and GUI tests pass.
2. **Add Wi-Fi locally:** validate fresh access-point discovery, permissions, and typed signal output on one Pi before adding networking.
3. **Add one agent and the registry:** send both radio results and heartbeats to a server; verify isolated failures and clean shutdown.
4. **Build the floor dashboard with nine simulated nodes:** verify locations, counts, radio switching, stale states, and overlapping observations.
5. **Pilot two physical Pis, then deploy nine:** test the real network and Wi-Fi uplink while scanning, then enable startup services.

Acceptance checks:

- Bluetooth discovery and JSON CLI output work through `scan_bluetooth.py`.
- A Wi-Fi scan waits for completion and excludes stale cached access points.
- Hidden/non-UTF-8 SSIDs are handled; identical SSIDs with different BSSIDs remain separate.
- Bluetooth and Wi-Fi show correct signal units and separate counts.
- One radio times out or is denied permission without stopping the other radio or heartbeats.
- Empty successful scans clear only the appropriate result set; heartbeats cannot refresh old detections.
- Unplugging one Pi affects only that node; changing its IP preserves its location.
- Server outage/restart does not stop agents or replay old observations as fresh.
- Malformed reports, oversized bodies, and invalid API keys leave other modules unaffected.
- Browser layout works on a PC and phone, including offline/error states and JSON export.
- The actual service user can scan both radios; the Pi's reporting connection remains usable while scanning Wi-Fi.

## 8. Deliberately deferred

Persistent history, LAN-client inventory, traffic capture, exact device positioning, cross-radio identity matching, automatic node enrollment, remote fleet controls, a visual layout editor, and redundant servers can be added later without changing the scanner/agent/server separation.
