# Physical deployment and acceptance record

**Current status: NOT RUN.** This is a blank evidence sheet for actual hardware, not an automated-test result. No Pi addresses, SSH access or physical deployment have been supplied. Complete it while following [deployment.md](deployment.md), T7 and T8 in [test-plan.md](../test-plan.md). Unit tests and simulated nodes do not pass these checks.

Use PASS / FAIL / NOT RUN. Record observed values, time, machine and application commit. Omit keys and real discovered device identifiers from committed evidence; keep any private field notes outside Git.

## Pilot environment

| Item | Pi A: server + pi-01 | Pi B: pi-02 |
| --- | --- | --- |
| Application commit / VERSION | NOT RUN | NOT RUN |
| Pi model, RAM, OS, Python | NOT RUN | NOT RUN |
| Physical label / location | NOT RUN | NOT RUN |
| Source IP / reserved server URL | NOT RUN | NOT RUN |
| Reporting uplink (Ethernet / Wi-Fi) | NOT RUN | NOT RUN |
| Bluetooth adapter / Wi-Fi interface | NOT RUN | NOT RUN |
| Service user and permission changes | NOT RUN | NOT RUN |
| Known BLE + discoverable Classic check | NOT RUN | NOT RUN |
| Known AP / hidden-name handling check | NOT RUN | NOT RUN |
| Server-only standard-library startup | NOT RUN | Not applicable |

## Two-Pi acceptance — T7

| Check | Result | Observed evidence / time |
| --- | --- | --- |
| T7.1 Repeat installation; PC opens actual server IP:8001 | NOT RUN | |
| T7.2 Both standalone scanners succeed as service user radio | NOT RUN | |
| T7.3 30-minute run, Wi-Fi scan and uplink coexist | NOT RUN | |
| T7.4 Agent crash restarts; intentional stop stays stopped | NOT RUN | |
| T7.5 Pi B reboot returns same ID; Pi A reboot recovers server | NOT RUN | |
| T7.6 Uplink loss/IP change preserves identity; no old scan replay | NOT RUN | |
| T7.7 Logs, status, restart and prior-version rollback repeatable | NOT RUN | |

| 30-minute sample | Pi A BT / Wi-Fi state and result age | Pi B BT / Wi-Fi state and result age | Heartbeat ages / upload errors / notes |
| --- | --- | --- | --- |
| Start | NOT RUN | NOT RUN | NOT RUN |
| 15 minutes | NOT RUN | NOT RUN | NOT RUN |
| 30 minutes | NOT RUN | NOT RUN | NOT RUN |

Maximum observed report gap: **NOT RUN**. Recovery durations after crash, reboot and network loss: **NOT RUN**. Pilot result and unresolved items: **NOT RUN**.

## Node-by-node installation — T8.1

Keep the full ID/key match in private configuration; this record checks that each node has a unique key without writing it here. Confirm both radios, or explicitly state a disabled radio and the unresolved requirement.

| Node | Floor | Physical label / real location / marker verified | Unique ID/key and correct URL | Current IP | Boot service / version | Bluetooth / Wi-Fi | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pi-01 | 1 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-02 | 1 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-03 | 1 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-04 | 2 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-05 | 2 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-06 | 2 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-07 | 3 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-08 | 3 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| pi-09 | 3 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

## Per-floor review — T8.2

| Floor | Three markers correct and clickable | Radio counts/details checked | Shared observation retains all reporting nodes | Result / unresolved items |
| --- | --- | --- | --- | --- |
| 1 | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| 2 | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| 3 | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

## Full-system acceptance — T8.3–T8.5

| Check | Result | Observed evidence / time |
| --- | --- | --- |
| One non-server Pi removed; other eight and GUI stay available | NOT RUN | |
| Removed Pi returns at same location with newly completed scans | NOT RUN | |
| All nine run for at least 60 minutes without accumulating history/work | NOT RUN | |
| Select every marker, switch radio and inspect shared observations | NOT RUN | |
| Export valid JSON, explain fresh-zero / stale / error / offline states | NOT RUN | |
| Browser server-disconnection indication and recovery | NOT RUN | |

Record cumulative CPU time or sampled CPU percentage consistently, and units for every memory value:

| 60-minute sample | Server CPU / memory | Agent CPU / memory, all nodes | Max heartbeat/report gaps / errors | Browser response |
| --- | --- | --- | --- | --- |
| Start | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| 30 minutes | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| 60 minutes | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

Remaining failures, disabled required radios, or missing tests: **NOT RUN**.

Final physical deployment result / date / reviewer: **NOT RUN**.
