# Step-by-step development plan

Architecture: [system-plan.md](system-plan.md). Verification: [test-plan.md](test-plan.md).

Execution update: the owner has authorized proceeding through every stage on branch `development/building-radio-system`, choosing simple defaults without further questions. Preserve the stage boundaries, tests, demonstrations, and separate commits below. Record unavailable physical-Pi checks as NOT RUN while continuing software development. See [development-progress.md](development-progress.md) for evidence and remaining work.

## How each stage works

1. State the stage, its purpose, and the files expected to change.
2. Implement only that stage. Keep the existing working app usable.
3. Run its tests and the relevant regression tests from the test plan.
4. Give the owner exact commands to repeat the demo, expected output, changed files, and known limitations.
5. Record the results in the stage review log below. Separate automated, simulated, and physical-device results; an unperformed test is not a pass.
6. Record the checkpoint and push the stage commit before proceeding under the owner's standing authorization. Hardware checks that cannot run remain explicitly pending, rather than being counted as passes.

Keep each stage as a small, separately reviewable Git change. Avoid mixing unrelated cleanup or later-stage features into it. Changes to the architecture or dependencies must be explained at the stage review.

## Current starting point

- `scan_bluetooth.py`, its CLI, and a single-machine browser GUI already exist.
- Bluetooth uses BlueZ and `dbus-fast`; the web server and tests use Python's standard library.
- There is no `scanner.py` compatibility wrapper.
- Wi-Fi scanning, agents, the receiving server/registry, and the building dashboard are not implemented yet.
- Previous checks reported 34 passing tests. Stage 1 establishes a fresh baseline; this document does not claim new test results.

Keep Python 3.11+, one pip dependency (`dbus-fast`) for scanner nodes, and plain HTML/CSS/JavaScript. Server-only operation should require only Python's standard library. No database, frontend framework, package bundler, broker, or container is needed initially.

## Stage map

| Stage | Small deliverable | What the owner can inspect | Matching tests |
| --- | --- | --- | --- |
| 1 | Verify existing Bluetooth scanner | Terminal/JSON results and current GUI | T1 |
| 2 | Standalone Wi-Fi scanner | Nearby access points in terminal/JSON | T2 |
| 3 | Server receives sample reports | Raw dashboard JSON for one fake node | T3 |
| 4 | One agent sends real scans | One node's Bluetooth and Wi-Fi on the server | T4 |
| 5 | Failure handling | Other radios/nodes continue when one fails | T5 |
| 6 | Three-floor browser dashboard | Nine simulated Pi markers and their results | T6 |
| 7 | Two-Pi pilot and startup services | Real reports, reboot recovery, remote browser access | T7 |
| 8 | Gradual rollout to nine Pis | Three working modules on each floor | T8 |

## Stage 1 — Verify `scan_bluetooth.py`

**Purpose:** establish a working local Bluetooth baseline before adding anything else.

Review the existing scan API, CLI, and tests. Fix only problems found by the baseline checks. Preserve Classic/BLE discovery, merging of repeated updates, delayed names, current-scan filtering, JSON formatting, and discovery cleanup on timeout/error/Ctrl+C. The current GUI must still work.

**Files:** `scan_bluetooth.py`, `tests/test_scan_bluetooth.py`; update `README.md` only if the instructions need correction.

**Demo:** run a timed scan and JSON scan with a known BLE advertiser and a discoverable Classic device nearby. Start the existing GUI, select a result, export it, and stop a scan.

**Checkpoint:** T1 passes, the owner can repeat the commands, and any unavailable hardware checks are recorded. Bluetooth is verified, not rewritten into a new abstraction.

## Stage 2 — Build and test `scan_wifi.py` locally

**Purpose:** verify Wi-Fi independently before introducing network reporting.

Add a separate module with `async scan(timeout, stop_event=None)` and an injectable backend for tests. Follow the small Bluetooth calling convention while keeping Wi-Fi properties separate. Add a CLI with `--timeout` and `--json`; progress/errors go to stderr. Make the default timeout 15 seconds.

Use NetworkManager through the existing `dbus-fast` dependency. Select a configured interface or the first managed Wi-Fi interface. Subscribe before requesting a scan, wait for a new `LastScan`, then read access points. Apply the `LastSeen` freshness rules and timestamp-unit conversion from the system plan. Do not present old cached APs as a fresh scan.

Return BSSID, SSID, raw SSID bytes encoded for JSON, frequency, recognized channel, signal quality in percent, advertised security, and other exposed properties. Missing values remain unknown. Wi-Fi here means nearby access points, not connected phones/laptops. Do not disconnect or reconfigure the Pi's network.

**Files:** new `scan_wifi.py`, `tests/test_scan_wifi.py`; update `README.md`. Keep backend error/cleanup logic within the radio module; do not add a generic plugin system.

**Demo:** compare a known access point's BSSID with text and JSON output on one Pi. Confirm Bluetooth still works separately. Test Wi-Fi under the user account intended to run the agent.

**Checkpoint:** T2 and Bluetooth regressions pass. The owner can run each scanner independently. No agent, uploads, or building UI in this stage.

## Stage 3 — Receive sample data on the server

**Purpose:** make the receiving side inspectable before connecting a real scanner.

Add `registry.py` to hold node configuration, latest per-radio results, receipt times, and derived health in memory. Extend `web_app.py` with an explicit server mode, `POST /api/reports`, and `GET /api/dashboard`. Preserve the existing local GUI mode. Move radio imports into local mode so server mode runs without `dbus-fast`, BlueZ, NetworkManager, or radio hardware.

Use example JSON configuration with node ID, floor, label, percentage coordinates, and placeholder API keys. Real configuration uses ignored `*.local.json` files. The server owns location data; an agent's IP may change without moving its marker. Get the reporting IP from the connection, not an untrusted payload field.

Before writing the agent, document and review the small report contract:

| Report | Required content |
| --- | --- |
| Successful scan | Node ID, kind `scan`, radio, success status, observation list (possibly empty) |
| Failed scan | Node ID, kind `scan`, radio, error status, short error message |
| Heartbeat | Node ID, kind `heartbeat`, enabled radios; no repeated observations |

Send the per-node key in the authorization header. Validate identity, radio, field types, and the 1 MiB request limit before updating state. Return useful HTTP errors. Dashboard responses never contain API keys. Use server monotonic receipt times, not Pi clocks.

Add a small development-only helper, `tools/demo_reports.py`, to send deterministic sample reports using standard-library HTTP. Start with one fake node; it must work without scanner imports. This helper will also support the nine-node UI demo later.

**Files:** new `registry.py`, registry/report tests, `config/server.example.json`, `tools/demo_reports.py`; changes to `web_app.py` and its tests. Document the exact JSON contract in `README.md` alongside the new commands.

**Demo:** send a heartbeat, two Bluetooth observations, one Wi-Fi observation, an empty Bluetooth result, and a Wi-Fi error. Inspect `/api/dashboard` after each. Counts and stale states must match T3.

**Checkpoint:** T3 passes, including server-only startup with no scanner dependency. The owner approves the report examples and can watch sample data reach the server. The building UI waits until Stage 6.

## Stage 4 — Send real results from one agent

**Purpose:** connect the two tested scanners to the tested server.

Add `agent.py` and an example agent configuration: stable node ID, key, server URL, enabled radios, and optional adapter/interface and timing settings. Add configured Bluetooth adapter selection here if needed, preserving the existing default selection. Do not duplicate the server's location table in the agent.

Run independent bounded asynchronous radio loops: Bluetooth scans for 15 seconds then pauses for 5; Wi-Fi starts every 60 seconds without overlapping requests. Send a report after each completed attempt and a heartbeat every 20 seconds. Catch radio failures separately.

Use standard-library HTTP outside the radio event loop/callbacks with a 3-second upload timeout. Bound concurrent upload work, preserve ordering within each radio, and discard failed reports. Do not add a persistent queue or replay old scans after reconnection. Keep Ctrl+C/SIGTERM shutdown and resource cleanup predictable.

**Files:** new `agent.py`, `tests/test_agent.py`, `config/agent.example.json`; small scanner changes only for explicit configuration needs.

**Demo:** run one agent and the server as separate processes on the development machine or one Pi. Watch both radio results and heartbeat status change in raw dashboard JSON. Disable one radio in configuration and show it as disabled.

**Checkpoint:** T4 passes. The owner can trace one result from scanner output through an HTTP report to server state. Start and stop each process independently before proceeding.

## Stage 5 — Prove failure isolation and recovery

**Purpose:** make problems visible without bringing down unrelated modules.

Complete the health/freshness rules in the system plan: node offline after 60 seconds without any report; Bluetooth stale after 60 seconds without a successful result; Wi-Fi stale after 150 seconds. An error immediately marks that radio's retained results stale. Heartbeats and the other radio never refresh detection results. Disabled and never-reported states stay distinct from an empty successful scan.

Use two simulated nodes to prove isolation. Test scanner exceptions, cancellation, stalled calls, unavailable server, slow uploads, malformed requests, and server restart. Fix only issues revealed by these tests. Keep result storage bounded to current snapshots and logs bounded; no history or retry backlog.

**Files:** agent/registry/server tests and necessary fixes in their modules; extend the sample-report helper for reproducible failure scenarios.

**Demo:** fail one radio, stop one simulated node, then stop/restart the server. Show which state changes and which continues. Recovery must use newly completed scans.

**Checkpoint:** T5 passes. The owner can see the difference between zero results, stale results, a failed radio, and an offline node before UI work starts.

## Stage 6 — Build the three-floor dashboard

**Purpose:** display the already-tested server state without new scanning logic in the frontend.

Add a small SVG building with Floor 1 at the bottom and three configured Pi markers per floor. Add a Bluetooth/Wi-Fi selector, per-node counts, node details, result details, JSON export, and an All observations view. Show both text and colour for health states. Current counts for stale/unavailable data show `—`; a fresh empty scan shows `0`.

Clicking a marker reveals that node's location, source IP, heartbeat age, both radio states, and selected-radio observations. Group Bluetooth by address/address type and Wi-Fi by BSSID; keep per-node measurements separate. Never combine the two radios into a physical-device total. Distinguish Wi-Fi percent from Bluetooth dBm.

Poll `/api/dashboard` every 2 seconds, more slowly in a hidden tab. Use text-safe rendering and mark the last view outdated when disconnected. Preserve the current local scanning GUI as a separate mode; the building view has automatic agents rather than fleet Start/Stop controls.

**Files:** `static/`, any required mode-specific asset routing, and sample fixtures/helper for nine simulated nodes. No frontend packages or build step.

**Demo:** use nine simulated nodes with known counts and overlapping observations. Click every marker, switch radios, export results, and demonstrate empty/error/offline states in the browser.

**Checkpoint:** T6 passes and the owner approves the layout and interaction. No fleet deployment yet.

## Stage 7 — Pilot with two physical Pis

**Purpose:** prove real networking, permissions, startup, and recovery before copying to nine devices.

Use Pi A as server plus an independent local agent, and Pi B as a second agent. Reserve the server IP through DHCP; give both agents stable IDs and unique keys. Open `http://<server-IP>:8001` from the owner's PC. `0.0.0.0` is a bind setting, not the browser destination.

Add separate agent/server `systemd` units, restart on failure with a short delay, and normal-user permissions. Intentional stops remain stopped. Document installation, configuration, logs, restart, and rollback to the last accepted version. Test scanning under the actual service account and test reporting over the same Wi-Fi interface used for discovery.

**Files:** example service units under `deploy/`, installation instructions, and only fixes found in the pilot. Keep actual keys and location configuration outside Git.

**Demo:** view both real nodes; crash one agent process; stop it intentionally; reboot each Pi; temporarily interrupt Pi B's reporting connection. Record a 30-minute normal-operation run and a separate recovery run.

**Checkpoint:** T7 passes and the owner accepts the two-node setup. Document that losing the server Pi's power also loses the dashboard; another agent cannot prevent that.

## Stage 8 — Roll out to nine Pis gradually

**Purpose:** expand the accepted pilot without changing the architecture.

Finalize `pi-01` through `pi-09`, three per floor. Verify every physical label, configured location, and marker position. Add and check one Pi at a time. Review each floor's three nodes before adding the next floor.

**Deliverables:** deployment/location checklist, completed configuration on each Pi, and results for T8. Use the same accepted application version; hardware defects should not trigger unrelated features.

**Demo:** click all nine markers, verify both radios or explicit disabled states, remove one non-server Pi temporarily, and confirm all other nodes continue reporting. Measure server CPU/memory and browser responsiveness over at least 60 minutes; record actual values and investigate sustained growth or repeated missed reports before acceptance.

**Checkpoint:** T8 passes and the owner accepts the deployment. Persistent history, precise device location, LAN-client inventory, remote fleet controls, and redundant servers remain later work.

## Stage review log

Update this table during implementation, not in advance. Passing a test does not automatically approve the next stage.

| Stage | Development status | Test evidence / hardware checks pending | Owner decision |
| --- | --- | --- | --- |
| 1 | Existing implementation; verification not started | Not run for this plan | Pending |
| 2 | Not started | Not run | Pending |
| 3 | Not started | Not run | Pending |
| 4 | Not started | Not run | Pending |
| 5 | Not started | Not run | Pending |
| 6 | Not started | Not run | Pending |
| 7 | Not started | Not run | Pending |
| 8 | Not started | Not run | Pending |

At each review record: application commit/version, changed files, commands used, test results, a short demo result, unresolved issues, and the owner's decision. Keep raw device exports and keys out of review notes committed to Git.
