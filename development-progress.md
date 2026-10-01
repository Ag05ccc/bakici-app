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

## Remaining stages

| Stage | Software work | Physical acceptance |
| --- | --- | --- |
| 2 — Wi-Fi | Implemented and checked on development PC | Pending Pi scan under service user |
| 3 — Receiving server | Not yet accepted | Not required for synthetic API checks |
| 4 — Reporting agent | Not started | Pending Pi end-to-end checks |
| 5 — Failure isolation | Not started | Network/power checks pending pilot |
| 6 — Building dashboard | Not started | Owner walkthrough pending |
| 7 — Two-Pi pilot | Not started | NOT RUN — physical Pis/access unavailable |
| 8 — Nine-Pi deployment | Not started | NOT RUN — physical Pis/access unavailable |
