# Acceptance evidence index

This index maps [test-plan.md](../test-plan.md) to observed evidence. The current branch has **146 passing automated tests**, **15 passing real-browser checks**, live Ubuntu Bluetooth/Wi-Fi reporting, and a one-minute nine-node synthetic monitoring run. Full commands, counts and limitations are in [development-progress.md](../development-progress.md).

PASS below means the stated software scope passed. PARTIAL and NOT RUN are not completed physical acceptance. No Raspberry Pi was available for deployment; no hardware or operator results are inferred from simulation.

| Check | Status | Evidence / outstanding scope |
| --- | --- | --- |
| T1.1 | PASS | Existing scanner/web suite rerun with warnings as errors. |
| T1.2 | PASS | `tests/test_scan_bluetooth.py`: cache exclusion, repeated events, delayed names. |
| T1.3 | PASS | Bluetooth argument/format tests, typed/binary JSON; live JSON parse. |
| T1.4 | PASS | Bluetooth failure, cancellation, startup/stop timeout, service loss tests; live SIGINT exit 130. |
| T1.5 | PARTIAL | Live PC discovery worked; known BLE/Classic fixtures on a physical Pi NOT RUN. |
| T1.6 | PARTIAL | Real Chrome with a synthetic scanner verifies Start, streaming/merged updates, delayed names/details, shared scans across tabs, duplicate-start rejection, Stop, partial JSON export, and clean restart. Owner's full hardware GUI walkthrough NOT RUN. |
| T2.1 | PASS | `tests/test_scan_wifi.py`: accepted request waits, early completion and invalidation races. |
| T2.2 | PASS | Boot-time unit conversion, stale/unknown AP filtering and boundary tests. |
| T2.3 | PASS | BSSID deduplication, hidden/non-UTF-8 SSIDs, binary properties and unknown channel tests. |
| T2.4 | PASS | Percentage signal, security flag interpretation, retained advertised bitrate properties. |
| T2.5 | PASS | Fake service/interface/permission/busy/disconnection/timeout cases. |
| T2.6 | PASS | Cancellation at connect/request/wait/read, cleanup, CLI and real SIGINT-handler tests. |
| T2.7 | PARTIAL | Live PC scan worked over an Ethernet reporting uplink; Pi service-user/known-AP check NOT RUN. |
| T3.1 | PASS | `python3 -S` server subprocess, receiver tests, and local GUI regression. |
| T3.2 | PASS | `tests/test_registry.py`: invalid configuration, uniqueness, coordinates, IP change. |
| T3.3 | PASS | Registry/HTTP tests reject invalid payloads, identities/keys, sizes and types atomically. |
| T3.4 | PASS | Actual HTTP sample sequence and snapshot replacement tests. |
| T3.5 | PASS | Error retains stale observations; empty success clears only one radio. |
| T3.6 | PASS | Private-field exclusion, snapshot/size bounds, no configuration/static traversal tests. |
| T3.7 | PASS | Concurrent sender/poll tests; slow-body timeout does not block a valid sender. |
| T4.1 | PASS | `tests/test_agent.py`: strict configuration, disabled radios, selections, secret-safe errors. |
| T4.2 | PASS | Independent timing/heartbeat tests with injectable waits/clock. |
| T4.3 | PASS | Successful/empty/error reports and heartbeat schema tests. |
| T4.4 | PASS | Slow upload lanes, bounded workers, dropped reports, delayed DNS, no redirects/replay tests. |
| T4.5 | PASS | Cooperative stop, deadline cancellation and upload shutdown tests; live agent SIGTERM exit 0. |
| T4.6 | PASS on PC | Separate live scanner agent and server produced fresh results for both radios. Pi path remains pending T7. |
| T5.1 | PASS | Agent tests stall either radio while the other and heartbeat continue. |
| T5.2 | PASS | Registry boundary tests and two-agent actual HTTP isolation. |
| T5.3 | PASS | Independent scan/heartbeat receipt times and exact stale limits. |
| T5.4 | PASS | All six radio states, including disabled-to-enabled without reviving old data. |
| T5.5 | PASS | Actual HTTP server outage while synthetic scanners continue; failed requests discarded. |
| T5.6 | PASS | New registry starts empty; heartbeat restores liveness only; later scans restore results. |
| T5.7 | PASS | Freshness derives solely from injected server monotonic receipt time. |
| T5.8 | PASS | Rejected bad reports preserve good node state while valid reports/polls continue. |
| T6.1 | PASS | Real Chrome: three floors ordered 3/2/1, nine markers, desktop and 390px screenshots. |
| T6.2 | PASS | Every marker clicked with both radio views; counts/location/rows checked against API. |
| T6.3 | PASS | Registry grouping tests plus real-browser shared reporter/signal details. |
| T6.4 | PASS | Browser checks fresh zero, error, stale, offline, waiting, disabled and count dashes. |
| T6.5 | PASS | Browser network outage marks retained view outdated; reconnect restores counts. |
| T6.6 | PASS | Hostile names/SSIDs render as text, property details/binary/Unicode render safely. |
| T6.7 | PASS | Browser-captured node/all JSON exports match scope, health and displayed observations. |
| T6.8 | PASS | No external asset requests or JS exceptions; local GUI readiness and complete scan workflow verified with synthetic observations in real Chrome. Hidden poll interval inspected in source. |
| T7.1 | NOT RUN on Pis | Install instructions, generator and units prepared; server-only desktop mode verified. |
| T7.2 | NOT RUN | Actual Pi service-account permissions require installed hardware. |
| T7.3 | NOT RUN | 30-minute physical dual-radio/Wi-Fi-uplink run requires two Pis. |
| T7.4 | NOT RUN | Systemd crash/restart/intentional-stop on Pi; unit syntax alone is not runtime proof. |
| T7.5 | NOT RUN | Actual Pi reboot/startup recovery. |
| T7.6 | NOT RUN | Actual uplink loss and DHCP address change. Registry identity logic is separately tested. |
| T7.7 | NOT RUN on Pis | Operations/rollback documented; restoration on installed hardware pending. |
| T8.1 | NOT RUN | Physical labels, rooms, unique node installation and startup on nine Pis. |
| T8.2 | NOT RUN | Physical three-nodes-per-floor walkthrough; nine simulated markers already verified. |
| T8.3 | NOT RUN | Physical loss/restoration of one non-server Pi; synthetic isolation separately verified. |
| T8.4 | NOT RUN physically | One-minute desktop synthetic smoke run passed; 60-minute nine-Pi run remains pending. |
| T8.5 | NOT RUN physically | Browser interactions tested with fixtures; owner/fleet walkthrough pending. |

Use [deployment-checklist.md](deployment-checklist.md) for actual hardware results. The application and deployment preparation are committed to the development branch; physical deployment remains an unfinished part of the original plan.
