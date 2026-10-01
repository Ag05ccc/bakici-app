# Development progress

Branch: `development/building-radio-system`. Baseline: `7c9be08`.

The owner authorized completing the stages without additional approval questions. Each stage is implemented, checked, and committed separately. This log distinguishes development-machine checks from physical Raspberry Pi acceptance. No Pi addresses or deployment access are configured in this repository.

## Stage 1 — Bluetooth baseline

Environment: Ubuntu development PC, x86_64, Python 3.12.3. BlueZ and NetworkManager are active; no Raspberry Pi was used.

- PASS: `.venv/bin/python -W error -m unittest discover -s tests -v` — 34 tests. Covers collection, fresh discovery, cleanup/failures, CLI, and the local web service/API.
- PASS: `node --check static/app.js`.
- PASS: a live 15-second Bluetooth JSON scan returned 6 records and exit 0. Raw addresses were not saved to Git.
- PASS: interrupting a live 30-second scan after 2 seconds returned valid partial JSON (4 records) and exit 130.
- NOT RUN: discovery of a deliberately configured Classic device and BLE advertiser on an actual Pi; owner-operated browser walkthrough. Available nearby devices are not a controlled Classic/BLE fixture.

Repeatable demo commands are in T1 of [test-plan.md](test-plan.md). No baseline code changes were needed.

## Stage 2 — Standalone Wi-Fi

- Implemented `scan_wifi.py` and its CLI with the existing `dbus-fast` dependency. Bluetooth and Wi-Fi are separate importable modules.
- PASS: 27 fake-backend Wi-Fi tests, covering completion races, fresh cache filtering, binary/hidden SSIDs, channel/security formatting, permissions, timeout, and cancellation.
- PASS: existing Bluetooth and local web tests (34 tests); combined scanner/local-GUI suite: 61 tests.
- PASS: live Ubuntu Wi-Fi scan returned 12 fresh AP records, valid JSON, preserved raw SSID bytes, and signal percentages in range. No AP names or addresses were committed.
- NOT RUN: a known-AP scan on a physical Pi under its service account or on a Wi-Fi reporting uplink; this PC currently reports over Ethernet.

Repeat: `.venv/bin/python scan_wifi.py --timeout 15 --json`. See T2 for individual failure checks.

## Stage 3 — Receiving server and sample reports

- Added a validated, thread-safe in-memory registry and explicit `web_app.py --mode server`; local Bluetooth GUI mode remains available.
- Added nine-node example configuration, a step-by-step sample sender, and [report protocol](docs/report-protocol.md).
- PASS: 35 registry, receiver, and helper tests under `python3 -S -W error` (site packages disabled), including fresh/error/empty semantics, authentication, malformed/oversized requests, concurrent senders, and slow-body isolation.
- PASS: a separate server process with site packages disabled accepted all five sample steps; nine configured nodes appeared with the expected Bluetooth zero and retained Wi-Fi error result. SIGTERM closed the server cleanly.
- PASS: existing local GUI tests after moving scanner imports into local mode.

Repeat the server/helper commands in the protocol document. No radio hardware is required for this stage's API checks.

## Stage 4 — One reporting agent

- Added independent radio/heartbeat loops, bounded upload workers, strict configuration, and optional Bluetooth adapter selection. See [agent guide](docs/agent.md).
- PASS: 19 agent tests without site packages, including real local HTTP delivery, scheduling, both directions of radio failure, missing dependency isolation, bounded uploads, no backlog, delayed DNS, and shutdown.
- PASS: 25 Bluetooth tests including explicit adapter selection and no silent fallback for a missing/off configured adapter.
- PASS: a live agent and separate server process on Ubuntu produced an online node, fresh Bluetooth (4 observations) and Wi-Fi (18 APs), plus heartbeat state. SIGTERM stopped the agent with exit 0. Raw discovery data was not committed.
- NOT RUN: the same path on Raspberry Pi hardware or its service account.

## Stage 5 — Failure isolation and recovery

- Added `tests/test_system.py` for actual HTTP integration between independently scheduled agents and the registry, using synthetic radio records.
- PASS: one radio error leaves the other radio and another agent working; stopping one agent makes only that node offline at the configured deadline.
- PASS: server outage leaves scanning active; a new server registry starts empty and receives later sequence numbers after recovery. A heartbeat alone never restores previous detections.
- PASS: combined registry/agent/system suite — 46 tests under `python3 -S -W error`, including monotonic boundary checks, independent radio ages, disabled/re-enabled state, request rejection, bounded upload lanes, and cancellation.
- NOT RUN: physical radio removal, Pi power/network loss, or systemd restart behaviour on actual Pis; these remain pilot checks.

Repeat: `python3 -S -W error -m unittest discover -s tests -p 'test_system.py' -v`. Exact count assertions use synthetic records; they do not depend on changing nearby devices.

## Stage 6 — Three-floor building dashboard

- Added a separate building view with nine configured Pi markers, Bluetooth/Wi-Fi counts, node health, observation/property selection, fresh-only grouping, and contextual JSON export. Local Bluetooth GUI mode remains available.
- Added the continuous nine-node demo with pause/error/empty controls. See [dashboard guide](docs/dashboard.md).
- PASS: 11 real headless-Chrome checks through actual server/API fixtures: all markers and both radio counts, six distinct health states, shared reporters/signals, text-safe hostile names, properties and exports, 390px mobile layout, browser disconnection/recovery, local GUI readiness, and no JavaScript exceptions or external asset requests.
- PASS: desktop and mobile screenshots visually inspected; three floors remain legible and all markers are reachable.
- PASS: 11 report-demo tests and server asset routing regression. Building JavaScript syntax check passes. Frontend assets total 39,048 bytes without a build step or external libraries.
- PASS: all 126 tests through Stage 6 with warnings treated as errors. A timing assumption in the restart test was corrected to distinguish an in-flight report from replay; the three fault-integration tests then passed ten consecutive runs.
- NOT RUN: owner-operated walkthrough and actual nine-Pi data; browser checks used deterministic synthetic observations.

Repeat: `node tools/check_browser.mjs` on a development PC with Node and Chrome. No Node/Chrome installation is required on the Pi.

## Stage 7 — Deployment preparation; physical pilot pending

- Added separate nonroot agent/server systemd units with boot startup, restart-on-failure, and bounded shutdown; optional narrowly scoped NetworkManager scan authorization and journal caps.
- Added private matching configuration generation, installation/permission instructions, a physical evidence checklist, and repeatable 30/60-minute monitoring. See [deployment guide](docs/deployment.md).
- PASS: 7 configuration-generator tests with site packages disabled, covering unique matching keys, 0700/0600 permissions, no overwrite, safe failures, and no secret output.
- PASS: 13 monitoring tests, including two-node selection from a nine-node layout, failure/recovery reporting, process CPU/RSS, malformed responses, privacy-safe output, and Ctrl+C summaries.
- PASS: server unit verification with `systemd-analyze verify`. Agent unit syntax verified with a temporary copy substituting the local Python path; its production `/opt/bakici-app/.venv/bin/python` is not installed on this PC.
- NOT RUN: actual Pi installation, service-account scan permissions, service crash/intentional-stop/reboot tests, Wi-Fi uplink coexistence, rollback, and the 30-minute two-Pi run. No accounts, services, or network settings were changed on the development PC.

## Remaining stages

| Stage | Software work | Physical acceptance |
| --- | --- | --- |
| 2 — Wi-Fi | Implemented and checked on development PC | Pending Pi scan under service user |
| 3 — Receiving server | Implemented; 35 stdlib tests and process demo pass | Not required for synthetic API checks |
| 4 — Reporting agent | Implemented; simulated and live desktop end-to-end checks pass | Pending Pi end-to-end checks |
| 5 — Failure isolation | 46 registry/agent/system tests pass | Network/power checks pending pilot |
| 6 — Building dashboard | Implemented; real browser/synthetic nine-node checks pass | Owner walkthrough and physical nodes pending |
| 7 — Two-Pi pilot | Deployment/configuration/monitoring artifacts prepared and tested | NOT RUN — physical Pis/access unavailable |
| 8 — Nine-Pi deployment | Not started | NOT RUN — physical Pis/access unavailable |
