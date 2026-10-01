# Step-by-step test plan

Use with [development-plan.md](development-plan.md). Stage numbers T1–T8 match development stages 1–8. Behaviour comes from [system-plan.md](system-plan.md).

This document defines checks; it does not report that they have passed. Results are recorded in [development-progress.md](development-progress.md). Fix software failures before continuing. The owner has authorized proceeding through the stages without further questions; unavailable physical hardware checks remain **NOT RUN** and do not become passes through simulation.

## Test approach and evidence

- Use Python `unittest`, fake D-Bus responses, an injectable monotonic clock, and a local HTTP server on a temporary port. No new test package is required.
- Automated scanner tests must work without radio hardware. Server-only tests must also run in a Python environment without `dbus-fast`.
- Use deterministic synthetic observations for exact counts and grouping tests. Real discovery results can vary; verify known advertisers/APs rather than expecting every nearby device.
- Advance a fake clock for offline/freshness tests instead of waiting minutes in unit tests. Test timeout boundaries immediately before, at, and after each configured deadline; document an expired state at age greater than or equal to the limit.
- Reuse small fakes where useful; do not build a simulation framework. The development sample-report helper uses Python's standard library.
- After changing a module, run its stage tests and any affected integration tests. Run the full suite before submitting the stage for review. Never run planned test commands until their files exist.
- For browser stages, use an existing browser on the PC. JavaScript syntax checking with `node --check static/app.js` is optional on the development PC if Node is already installed; Node is not a Pi dependency.
- Run disruptive hardware checks only on pilot devices with a recovery path. Do not disable the sole remote connection while relying on it to restore access.

Record each check as **PASS**, **FAIL**, or **NOT RUN**, with the version/commit, machine, command or procedure, expected result, actual result, and short evidence. Keep unit/simulated results separate from physical Pi results. Use synthetic data in committed evidence, and omit real API keys.

## Commands available now

Run from the project root after the virtual environment is set up as described in `README.md`:

```bash
# Focused Bluetooth and existing GUI tests.
.venv/bin/python -m unittest discover -s tests -p 'test_scan_bluetooth.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_web_app.py' -v

# Complete regression suite.
.venv/bin/python -W error -m unittest discover -s tests -v

# Physical Bluetooth checks; JSON export remains ignored by Git.
.venv/bin/python scan_bluetooth.py --timeout 15
.venv/bin/python scan_bluetooth.py --timeout 15 --json > devices.json
.venv/bin/python -m json.tool devices.json

# Existing local GUI; open http://127.0.0.1:8001/ on this PC.
.venv/bin/python web_app.py --host 127.0.0.1 --port 8001
```

Stop the local GUI before using the same port for the later receiving server. For a remote browser use the server's actual LAN address and bind the server to `0.0.0.0`.

## Proposed commands for later stages

These interfaces must be delivered and documented by their stages; **they do not exist yet**. If a command changes during implementation, update both the demo instructions and this plan before review. Copy example configurations to ignored `*.local.json` files, then set matching IDs/keys and the server URL. Use test-only keys for simulated nodes.

| Available after | Command / action |
| --- | --- |
| Stage 2 | `.venv/bin/python scan_wifi.py --timeout 15` and the same command with `--json` |
| Stage 2 | `.venv/bin/python -m unittest discover -s tests -p 'test_scan_wifi.py' -v` |
| Stage 3 | `python3 web_app.py --mode server --config config/server.local.json --host 127.0.0.1 --port 8001` |
| Stage 3 | `.venv/bin/python -m unittest discover -s tests -p 'test_registry.py' -v` and `-p 'test_reports.py'` |
| Stage 3 | `python3 tools/demo_reports.py --server http://127.0.0.1:8001 --config config/server.local.json --scenario one-node` |
| Stage 4 | `.venv/bin/python agent.py --config config/agent.local.json` |
| Stage 4 | `.venv/bin/python -m unittest discover -s tests -p 'test_agent.py' -v` |
| Stage 6 | `python3 tools/demo_reports.py --server http://127.0.0.1:8001 --config config/server.local.json --scenario nine-nodes` |

The Stage 3 helper should let the owner submit each sample report separately and inspect the result before continuing. The Stage 6 scenario should provide repeatable updates and a documented way to pause one node's reports.

Inspect the receiving server without additional tools:

```bash
# Available after Stage 3, while the server runs in another terminal.
python3 - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:8001/api/dashboard', timeout=3) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

## T1 — Existing Bluetooth and local GUI

| Check | Procedure | Expected result |
| --- | --- | --- |
| T1.1 Automated baseline | Run the existing Bluetooth and web tests plus the full suite. | All tests pass; record the actual count. Existing functionality remains usable. |
| T1.2 Freshness and merging | Simulate cached entries, current discovery events, repeated updates, delayed names, and unrelated name/status changes. | Only current discovery evidence admits a device; one record per BlueZ object; later properties merge. Cached-only devices are excluded. |
| T1.3 Output and arguments | Test missing properties, binary data, unknown RSSI, empty results, and zero/negative/NaN/infinite timeouts. | Unknown values, hex encoding, typed JSON, sorting, and argument errors match the existing CLI contract. Stdout in JSON mode contains only JSON. |
| T1.4 Failures and cleanup | Simulate missing/off adapters, access denied, service loss, startup timeout, cancellation, and failed stop. | Clear errors; this app's discovery connection/session is released. Partial Ctrl+C results are preserved. |
| T1.5 Physical discovery | On a Pi, scan a known BLE advertiser and a known discoverable Classic device. Interrupt a longer scan, then scan again. | Both are observed when discoverable; output is readable/valid JSON; a new scan can start after interruption. Do not require fields the device does not advertise. |
| T1.6 Current GUI | Start a scan, inspect a row, stop, export, restart, and open a second browser tab. | Details/export agree with results; only one local scan runs; stopping keeps partial results. |

**Review evidence:** automated summary and one physical discovery/GUI demonstration. Hardware unavailable means T1.5 is NOT RUN, even if fakes pass.

## T2 — Standalone Wi-Fi

Add `tests/test_scan_wifi.py` with fake NetworkManager responses.

| Check | Procedure | Expected result |
| --- | --- | --- |
| T2.1 Completion and subscription race | Delay `LastScan` after an accepted request; also emit completion before the request reply. | No early result from an accepted-but-incomplete request, no missed completion, and bounded wait. |
| T2.2 Cached AP filtering | Mix new, old, unknown, and boundary `LastSeen` values. Use boot-time milliseconds for `LastScan` and seconds for `LastSeen`. | Only observations in the accepted scan window, including the documented one-second precision allowance, count as fresh. |
| T2.3 Identity and fields | Supply repeated BSSIDs, two BSSIDs with one SSID, hidden/non-UTF-8 SSIDs, unknown properties/frequencies, and binary data. | One result per BSSID; same-name APs stay separate; raw SSID is preserved; unknown channel stays unknown; JSON is valid. |
| T2.4 Signal and security | Supply representative signal values and security flags. | Signal is labelled percent, not dBm; missing information remains unknown; advertised bitrate is not described as measured throughput. |
| T2.5 Backend failures | Simulate absent NetworkManager, missing/unmanaged adapter, invalid configured interface, denied/busy request, disconnect, and stalled scan. | Useful Wi-Fi error within a bounded time; no false successful empty result. |
| T2.6 Cancellation and CLI | Cancel during connection/request/wait/read, then run again; test invalid timeouts and JSON stdout. | Tasks/subscriptions/connections are cleaned up; subsequent scans work. No network-disconnect or monitor-mode operation is invoked. |
| T2.7 Physical scan | Use a known AP on a Pi, under the intended service user; verify BSSID and repeat with JSON. Run Bluetooth again. | Real AP appears, output matches supported fields, Bluetooth remains working, and the Pi retains connectivity. |

**Review evidence:** standalone commands and results for both radio modules. On headless permission failure, document the specific permission needed; do not run the whole server as root to bypass it.

## T3 — Registry and receiving server

Add `tests/test_registry.py` and `tests/test_reports.py`. Start a local HTTP server in tests and use fake receipt times.

| Check | Procedure | Expected result |
| --- | --- | --- |
| T3.1 Server-only startup | Start server mode in a clean standard-library Python environment without `dbus-fast` or radio services. Run report tests there too. | Server/report API starts and works; no scanner import is required. Local mode still passes existing GUI tests in its normal environment. |
| T3.2 Configuration and identity | Load valid layout; try duplicate IDs, invalid floors/coordinates, and missing required key/config fields. Send a node from a changed source IP. | Invalid configuration gets a clear startup error; a valid node keeps its configured floor/location when its IP changes. |
| T3.3 Report validation | Submit valid reports, malformed JSON, invalid field types/radios, unknown IDs, missing/wrong/another node's key, and an oversized body. | Valid reports are accepted; invalid reports get documented non-success HTTP responses and do not change good results. |
| T3.4 Snapshot replacement | Send two Bluetooth records and one Wi-Fi AP, then an empty Bluetooth success. | Bluetooth becomes zero; Wi-Fi remains one. A repeat snapshot replaces rather than appends duplicates. |
| T3.5 Error versus empty | After Wi-Fi success, send a Wi-Fi failure. | Previous Wi-Fi results are retained with an error/stale label and excluded from current counts; error is not displayed as a successful zero. |
| T3.6 Private and bounded state | Read dashboard/static responses and errors; send repeated reports. | Keys are absent from responses/logs; only latest snapshots are retained; current limits bound incoming data. Server configuration files are not served as assets. |
| T3.7 Server concurrency | Send reports for two nodes while polling; hold a request open or send a slow body within a test deadline. | Well-formed reports/polls continue; request handling has bounded waits; state is consistent. |

**Owner demo sequence:** waiting node → heartbeat (online, no detections yet) → Bluetooth count 2 → Wi-Fi count 1 → Bluetooth count 0 → Wi-Fi error (current count `—`, previous result stale). Show raw dashboard JSON after each step.

## T4 — One reporting agent

Add `tests/test_agent.py`; inject fake scanners, uploader, scheduler/clock, and stop event.

| Check | Procedure | Expected result |
| --- | --- | --- |
| T4.1 Configuration | Try each enabled-radio combination, configured adapter/interface, invalid timing, missing ID/key, and bad server URL. | Valid configuration is honored; invalid configuration fails clearly; secrets are not printed. Disabled radios are not invoked and are declared in heartbeats. |
| T4.2 Scheduling | Advance test time through Bluetooth scans plus pauses, Wi-Fi intervals, and 20-second heartbeats. | Independent loops; no overlapping scan for one radio; heartbeats continue while a scan runs. |
| T4.3 Payloads | Complete a successful, empty, and failed scan. | Exactly that node/radio is reported with the agreed contract. Heartbeats contain no observations; error text remains bounded. |
| T4.4 Slow uploads and ordering | Delay or reject uploads and let the next scan finish. | Upload work is bounded and does not block scanning/heartbeats; old results cannot overwrite newer results due to concurrent same-radio uploads. Failed uploads are discarded. |
| T4.5 Stop and restart | Stop during scanning and uploading, including immediately after process startup. | Work is cancelled/finished within documented bounds, Bluetooth releases discovery, and restart succeeds without duplicate loops. |
| T4.6 Real one-node path | Run server and agent separately, compare local radio output and server snapshots over several cycles. | Both radios arrive under the correct node. Counts represent completed scans; disabled state and radio errors are visible. |

**Review evidence:** show one observation at each boundary: scanner record → report → server snapshot. Use a synthetic observation for an exact value comparison; real scans at different times may differ.

## T5 — Failure isolation, freshness, and recovery

Extend agent/registry/report integration tests. Use fake clocks for timing and two deterministic simulated nodes for isolation.

| Check | Procedure | Expected result |
| --- | --- | --- |
| T5.1 Radio independence | Raise an exception or hang one radio; keep the other completing scans. | The failed radio reports an error after a bounded deadline; the other radio and heartbeats continue. Test both directions. |
| T5.2 Node liveness | Stop all reports from one node; keep the second reporting. Test the 60-second boundary. | Only the silent node becomes offline. A scan report or heartbeat updates node liveness. |
| T5.3 Result freshness | Keep heartbeats arriving without new successful scans; test Bluetooth at 60 seconds and Wi-Fi at 150. | Results become stale independently even while the node stays online. Neither other-radio reports nor heartbeats refresh observations. |
| T5.4 State distinctions | Exercise never-reported, fresh-empty, disabled, failed, stale, and offline states. Disable a previously active radio. | Dashboard JSON distinguishes all states; stale/disabled/offline observations are excluded from current totals; `0` is reserved for a fresh empty success. |
| T5.5 Server outage | Refuse connections and simulate 3-second upload timeouts, then restore the server. | Scanners continue; upload work stays bounded; recovery sends newly completed scans without an offline backlog. |
| T5.6 Server restart | Restart after successful reports and reload the same layout. Send a heartbeat before any new scan. | Configured nodes begin waiting; heartbeat restores liveness only. No old detections reappear as fresh. |
| T5.7 Clock changes | Jump node/wall-clock time while keeping server monotonic time controlled. | Liveness and freshness are unaffected by unsynchronized Pi clocks or wall-clock jumps. |
| T5.8 Bad sender isolation | Repeatedly send malformed/oversized reports from one simulated node while another sends valid data. | Existing valid state remains intact and the good node keeps updating. This checks ordinary failure containment, not Internet-scale denial-of-service resistance. |

**Review evidence:** a state sequence showing which node/radio changed and which continued. An agent crash test here may use a subprocess; automatic service restart is checked on real Pis in T7.

## T6 — Building dashboard with nine simulated nodes

Extend the helper with nine configured test IDs. Supply deterministic distinct counts, a fresh empty scan, an offline node, and a radio error. Test backend aggregation automatically and browser interactions manually.

| Check | Procedure | Expected result |
| --- | --- | --- |
| T6.1 Layout | Load all nine configured nodes; resize the browser and use a narrow viewport. | Floor 1 is at the bottom; three correctly labelled markers per floor; all remain clickable and match configured coordinates. |
| T6.2 Selection and counts | Click each marker and switch Bluetooth/Wi-Fi. | Counts match that node and radio; details show the correct location, IP, ages, and both radio health states. No mixed radio totals. |
| T6.3 Shared observations | Send the same Bluetooth address/type from two nodes, a different address type, missing addresses, repeated BSSID, and two BSSIDs sharing an SSID. | Grouping follows the system plan; reporters and their signals are preserved. Missing Bluetooth addresses stay node-local; radios never merge identities. |
| T6.4 Stale and offline | Pause a node and fail one radio; then resume fresh reports. | Health labels, `—`/`0` counts, stale detail labels/ages, and recovery match server state. Stale observations do not inflate current All observations totals. |
| T6.5 Browser disconnection | Stop the server while the page is open, then restart it. | The page explicitly marks the last view outdated; polling recovers without a reload or showing cached detections as current. |
| T6.6 Safe text and details | Use names/SSIDs containing HTML-like text, quotes, Unicode, binary data, and long values. | Names display as text, execute nothing, and do not break layout. Unknowns and units are clear. |
| T6.7 Export and navigation | Select a node, change radio, export, refresh, and return to the view. | Export has a documented scope matching the displayed results, valid JSON, and clear node/radio/freshness context; no keys. No browser errors. |
| T6.8 Lightweight delivery | Inspect browser requests; run with external Internet unavailable. Test existing local GUI mode again. | Assets are local, no CDN/frontend dependency is required, polling is about every 2 seconds and slower when hidden, and local mode remains usable. |

**Review evidence:** owner-operated walkthrough with nine simulated nodes and a short checklist of expected versus shown counts. A screenshot alone does not verify live updates or click behaviour.

## T7 — Two physical Pis and services

Record Pi OS/Python versions, network connection type, radio hardware, application version, service user, and server URL. Use Pi A for server plus local agent, Pi B for agent only.

| Check | Procedure | Expected result |
| --- | --- | --- |
| T7.1 Installation and access | Follow setup on both Pis; open the server from the PC using its actual IP and port 8001. | Setup is reproducible with the documented packages, and the browser needs no Pi desktop. Server-only deployment does not require scanner dependencies. |
| T7.2 Service permissions | Run both radios as the actual systemd service user. | Scans/reporting succeed without running the entire app as root; required scan authorization is documented. |
| T7.3 Coexisting radios/uplink | Run both agents for 30 minutes, including a Pi reporting over the Wi-Fi interface it scans. | Heartbeats and fresh scans keep arriving; delays/errors are visible and recover; no forced reconnect or network reconfiguration. Record observed report gaps. |
| T7.4 Agent crash and stop | Crash only Pi B's agent process, then intentionally stop its unit. | Crash triggers service restart; an intentional stop stays stopped. Pi A's agent/server remain available. |
| T7.5 Reboots | Reboot Pi B, then Pi A in a separate test. | Services start at boot. Pi B returns without changing identity. During Pi A's reboot the dashboard is unavailable, Pi B keeps scanning, and only fresh results arrive after recovery. |
| T7.6 Network/IP change | Temporarily interrupt Pi B's uplink using a recoverable method; renew/change its IP while preserving its node ID. | Only Pi B goes offline; it returns at its configured location with updated source IP. Old failed uploads are not replayed. |
| T7.7 Service operations | Follow documented status/log/restart/rollback instructions. | Owner can diagnose a radio or agent failure and restore the last accepted version/configuration. Logs contain useful errors without keys and use bounded journal storage. |

**Review evidence:** two-Pi demonstration, completed 30-minute normal-operation record, and separate restart/outage results. Do not accept simulated permissions/reboots as substitutes for these checks.

## T8 — Nine-Pi acceptance

Apply the same accepted version and add one Pi at a time. Do not turn on all unverified nodes at once.

| Check | Procedure | Expected result |
| --- | --- | --- |
| T8.1 Per-node checklist | For each of `pi-01`–`pi-09`, verify physical label, floor/position, unique key/ID, server URL, current source IP, boot startup, and both radios or explicit disabled state. | Physical Pi and dashboard marker agree; no duplicate IDs or misplaced markers. |
| T8.2 Per-floor review | With three nodes on one floor reporting, click all three and compare known observations. Obtain owner review before adding the next floor. | Three working, correctly placed nodes per accepted floor. Overlapping observations retain their reporting nodes. |
| T8.3 Single-node loss | Remove power/network from one non-server Pi, then restore it. | Only that node becomes unavailable; eight other nodes and the GUI continue; restored results are fresh. |
| T8.4 Full-system run | Observe at least 60 minutes with all nine nodes. Record CPU/memory at start, midpoint, and end, report gaps, errors, and browser response. | Counts/details remain responsive; no sustained memory growth, unbounded work, unexplained repeated stale nodes, or accumulating report history. Investigate failures before approval. |
| T8.5 Final owner walkthrough | Select every marker, switch radio, inspect a shared observation, export results, and explain zero/stale/error/offline states. | Owner can inspect the system and diagnose a missing node using the supplied instructions. |

**Review evidence:** nine-row deployment checklist, one review per floor, full-system run results, remaining limitations, and the owner's final decision. Any disabled required radio or untested recovery remains an explicit unresolved item.
