# Deploy the building scanner in small steps

Status: deployment files are prepared; **physical Raspberry Pi checks are NOT RUN**. No services have been installed on the development PC. Record real results in [deployment-checklist.md](deployment-checklist.md) and follow T7/T8 in [test-plan.md](../test-plan.md).

Use Raspberry Pi OS Lite **64-bit**, Bookworm or later, with Python 3.11+. Set the hostname, SSH account, Wi-Fi country and network in Raspberry Pi Imager. NetworkManager is the standard network manager on these releases. A desktop, browser, Node.js, database and containers are unnecessary on the Pis. [Raspberry Pi networking documentation](https://www.raspberrypi.com/documentation/computers/configuration.html#networking)

Begin with Pi A (`pi-01`, server plus agent) and Pi B (`pi-02`, agent only). Reserve Pi A's address through your router's DHCP settings. Other Pis can use normal DHCP: identity and placement use the node ID, while the most recent source IP is diagnostic information. The dashboard runs on port 8001; open `http://SERVER_LAN_IP:8001/` on the PC. `0.0.0.0` is the server's listen setting, not a browser destination.

Use this initial HTTP deployment only on the trusted building LAN. Keep port 8001 off the public Internet. Node keys authenticate reports; HTTP does not encrypt them. The read-only dashboard is visible to clients that can reach it. No browser credentials or TLS proxy are required for the initial private-LAN setup.

## 1. Prepare layout, keys and one application version on the PC

From the project directory, generate one matching configuration set. Replace the example address with Pi A's actual reserved address:

```bash
python3 tools/make_configs.py --server-url http://192.168.1.50:8001 --output deployment.local
```

The generator uses `config/server.example.json`, makes nine unique random keys, and writes `deployment.local/server.local.json` plus `pi-01.agent.local.json` through `pi-09.agent.local.json`. It requires only Python's standard library. The directory is mode 0700 and files are 0600; secrets are not printed. An existing output path is refused, including an empty directory. Keep this ignored directory as the private configuration source; do not commit it or paste its contents into logs/issues.

Edit the generated server file to set real room labels and marker positions: `x`/`y` are percentages within each floor. Edit individual agent files only when selecting `bluetooth.adapter`, `wifi.interface`, disabling a radio, or changing intervals. Each enabled radio defaults to automatic interface selection. If intervals change, adjust server freshness limits accordingly. A disabled radio is explicit in heartbeats; missing hardware must not be hidden by an invented successful empty scan.

The server Pi's local agent may use `http://127.0.0.1:8001`; set that URL only in `pi-01.agent.local.json`. Keep every other agent pointed at the server's reserved LAN IP. Distribute only a node's own agent file to that node; only the server receives the server configuration.

Export a single reviewed branch version without copying your virtual environment or GitHub credentials to every Pi:

```bash
git archive --format=tar --output=/tmp/bakici-app.tar development/building-radio-system
git rev-parse development/building-radio-system > /tmp/bakici-app-version.txt
```

Copy that archive, version marker and the appropriate private config via SSH. Replace `ADMIN`, `PI_A` and `PI_B` with the configured SSH user and addresses:

```bash
scp /tmp/bakici-app.tar /tmp/bakici-app-version.txt ADMIN@PI_A:~/
scp deployment.local/server.local.json deployment.local/pi-01.agent.local.json ADMIN@PI_A:~/
scp /tmp/bakici-app.tar /tmp/bakici-app-version.txt ADMIN@PI_B:~/
scp deployment.local/pi-02.agent.local.json ADMIN@PI_B:~/
```

This avoids a GitHub login or additional Git tools on each Pi. An already authenticated Pi may instead clone the private repository and check out the same commit; install the same tracked files into `/opt/bakici-app` and record that commit.

## 2. Install the common files on each Pi

Run these commands over SSH on a new Pi. The `radio` service account is intentionally separate from the administrator login:

```bash
sudo apt update
sudo apt install -y python3
sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin radio
sudo install -d -o root -g root -m 0755 /opt/bakici-app
sudo tar --extract --file "$HOME/bakici-app.tar" --directory /opt/bakici-app --no-same-owner
sudo install -o root -g root -m 0644 "$HOME/bakici-app-version.txt" /opt/bakici-app/VERSION
sudo install -d -o root -g radio -m 0750 /etc/bakici
```

Create the account once; skip `useradd` when `id radio` already succeeds. The service account reads application files but does not own or modify them. Configuration in `/etc/bakici` is outside the web server's static-file directory.

For the **server role**, install its configuration on Pi A:

```bash
sudo install -o root -g radio -m 0640 "$HOME/server.local.json" /etc/bakici/server.json
```

A server-only Pi needs no scanner virtual environment, BlueZ or `dbus-fast`. It serves the dashboard using `/usr/bin/python3`.

For the **scanner role**, run on Pi A and Pi B:

```bash
sudo apt install -y python3-venv bluez
systemctl is-active NetworkManager
sudo systemctl enable --now bluetooth
sudo python3 -m venv /opt/bakici-app/.venv
sudo /opt/bakici-app/.venv/bin/python -m pip install --only-binary=:all: -r /opt/bakici-app/requirements.txt
```

NetworkManager should already control the configured network on this OS. If it is absent, record the OS/network configuration and resolve that before the pilot; do not replace a working remote network manager during this procedure. The single pip dependency is `dbus-fast`; using a binary wheel avoids installing a compiler.

Install the matching node's file. On Pi A use `pi-01`; on Pi B use `pi-02`:

```bash
sudo install -o root -g radio -m 0640 "$HOME/pi-01.agent.local.json" /etc/bakici/agent.json
```

After confirming the installed copies, delete the transfer copies of the private configuration from the administrator's home. Retain the protected master configuration on the PC. Do not regenerate keys independently on individual nodes.

## 3. Verify radio permissions as the actual service user

Before starting services, run one standalone scan of each radio as `radio`:

```bash
sudo -u radio /opt/bakici-app/.venv/bin/python /opt/bakici-app/scan_bluetooth.py --timeout 15
sudo -u radio /opt/bakici-app/.venv/bin/python /opt/bakici-app/scan_wifi.py --timeout 15
```

Use a known BLE advertiser, a Classic device in discoverable mode, and a known Wi-Fi access point. Record the observed results and any missing checks. A successful PC scan does not prove Pi service-user permissions. If Bluetooth is off, power it on through the Pi's OS administration tools, then repeat. Discovery does not pair with devices.

If Wi-Fi fails specifically because scan authorization is denied, inspect permissions with `sudo -u radio nmcli general permissions`. Optionally install the supplied narrowly scoped rule:

```bash
sudo install -o root -g root -m 0644 /opt/bakici-app/deploy/50-bakici-wifi-scan.rules /etc/polkit-1/rules.d/50-bakici-wifi-scan.rules
sudo -u radio /opt/bakici-app/.venv/bin/python /opt/bakici-app/scan_wifi.py --timeout 15
```

The rule permits only `org.freedesktop.NetworkManager.wifi.scan` for user `radio`; it does not authorize network reconfiguration. Polkit reloads rules when files change. Do not install the rule if existing permissions already suffice. [Polkit rule documentation](https://polkit.pages.freedesktop.org/polkit/polkit.8.html), [NetworkManager policy definitions](https://github.com/NetworkManager/NetworkManager/blob/main/data/org.freedesktop.NetworkManager.policy.in)

For a Bluetooth access-denied error, inspect the Pi's packaged BlueZ D-Bus policy and journal first. If that OS's policy explicitly grants the required Bluetooth access to its `bluetooth` group, add `radio` to that existing group and repeat the same scan. Avoid a generic allow-all D-Bus rule. Keep the agent and web server nonroot. Record any OS-specific permission change in the checklist.

## 4. Install separate startup services

On Pi A, install the server unit:

```bash
sudo install -o root -g root -m 0644 /opt/bakici-app/deploy/bakici-server.service /etc/systemd/system/bakici-server.service
```

On each scanner Pi, install the agent unit:

```bash
sudo install -o root -g root -m 0644 /opt/bakici-app/deploy/bakici-agent.service /etc/systemd/system/bakici-agent.service
```

Check the applicable units on that Pi, reload, then start the roles it hosts:

```bash
sudo systemd-analyze verify /etc/systemd/system/bakici-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now bakici-agent.service
```

On Pi A also run:

```bash
sudo systemd-analyze verify /etc/systemd/system/bakici-server.service
sudo systemctl enable --now bakici-server.service
```

An agent starting before the server is available continues scanning and retries with future results. The services have no dependency that stops the server when an agent fails. Each unit restarts after failure with a five-second delay; `systemctl stop` remains stopped. Shutdown sends SIGTERM, with a 25-second bound for the agent and 15 seconds for the server before systemd forcibly terminates a stuck process. [systemd service lifecycle documentation](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)

Logs use the existing system journal. On a dedicated Pi, optionally apply the supplied **machine-wide** journal storage caps (100 MiB persistent, 20 MiB volatile, seven-day retention):

```bash
sudo install -d -m 0755 /etc/systemd/journald.conf.d
sudo install -o root -g root -m 0644 /opt/bakici-app/deploy/journal-limits.conf /etc/systemd/journald.conf.d/bakici-limits.conf
sudo systemctl restart systemd-journald
```

These caps affect other services' journal retention too; a shared Pi can keep its administrator's existing bounded policy. [systemd journal configuration](https://github.com/systemd/systemd/blob/main/man/journald.conf.xml)

## 5. Inspect the two-Pi pilot before adding more nodes

Open `http://SERVER_LAN_IP:8001/` from the PC. The server binds to all its interfaces, but the browser uses the real IP. If access fails, verify the server service, correct IP/port, and that the PC and Pi can communicate through the local network/firewall. Uninstalled nodes remain waiting; this is expected.

Select `pi-01` and `pi-02`, switch Bluetooth/Wi-Fi, compare known results, and export a view. Inspect both radios' health and source IPs. Count `0` means a fresh empty scan; `—` means there is no current count. Stale/error/offline results can be retained for inspection and are excluded from current totals.

Run both nodes for **30 minutes**, with at least one Pi reporting over the same Wi-Fi interface it scans. Record radio states, maximum heartbeat/report gaps and errors at the start, midpoint and end. NetworkManager remains in control of the connection; scans must not switch networks or enable monitor mode. Ethernet is useful when already available, but a second adapter is not required.

After both pilot nodes and their enabled radios are fresh, run the read-only monitor from the PC project directory. Replace the address with the server's actual IP:

```bash
python3 -S tools/monitor_deployment.py --server http://192.168.1.50:8001 --duration 1800 --interval 10 --expected-nodes 2 --node pi-01 --node pi-02 > deployment.local/pilot-monitor.jsonl
```

The explicit node selection allows the other seven configured markers to remain waiting. Output contains sampled health, ages and a final summary, excluding observation properties and keys. Exit 0 means every sampled check passed; exit 1 means an unhealthy sample, invalid response or request/resource failure occurred; Ctrl+C exits 130 and leaves a partial summary. A polling sample cannot prove that no shorter interruption occurred between samples. Run deliberate outage tests separately from this normal-operation record.

Then test the recovery cases separately on Pi B, keeping console access or another recoverable administration path:

1. Crash only its agent: `sudo systemctl kill --signal=SIGKILL bakici-agent.service`. Verify automatic restart and continued service on Pi A.
2. Intentionally stop it: `sudo systemctl stop bakici-agent.service`. Verify it remains stopped and only that node becomes offline after 60 seconds. Restore with `sudo systemctl start bakici-agent.service`.
3. Reboot Pi B with `sudo reboot`; verify the same ID/location returns and boot services are enabled.
4. Briefly interrupt Pi B's uplink using a method you can reverse locally. Restore it, then verify the node returns with new scans. If DHCP assigns a different address, location and ID must remain unchanged. Avoid remotely disabling the only interface used for SSH.
5. Reboot Pi A separately. The dashboard is unavailable while its server is down; Pi B keeps scanning. The server rebuilds state from new reports rather than replayed observations.

Complete T7 and the pilot section of the checklist. Hardware, permissions, reboot and uplink tests remain **NOT RUN** until performed on those actual Pis.

## 6. Roll out one node and one floor at a time

Install the same archived commit and matching configuration on each remaining Pi. Verify its physical ID label, real floor/location, configured marker coordinates, service startup, IP, and both enabled radios before adding another node. Finish Floor 1 (`pi-01`–`pi-03`), then Floor 2 (`pi-04`–`pi-06`), then Floor 3 (`pi-07`–`pi-09`). Use the nine-row checklist instead of assuming an image copied to another SD card has the right identity.

With all nine reporting, observe at least **60 minutes**. Record server and agent CPU/memory at the start, 30 minutes and 60 minutes.

```bash
python3 -S tools/monitor_deployment.py --server http://192.168.1.50:8001 --duration 3600 --interval 10 --expected-nodes 9 > deployment.local/rollout-monitor.jsonl
```

Run this monitor command on the PC after all nine nodes and enabled radios are fresh. For automatic server process metrics, run the monitor on the server Pi instead and add `--pid SERVER_PID`, using the actual MainPID below and an output file in a protected writable directory. Omit `--pid` when monitoring a remote server: its process metrics remain unavailable, not zero. For each relevant Pi, also record these local resource values:

```bash
systemctl show bakici-agent.service -p MainPID -p MemoryCurrent -p CPUUsageNSec -p NRestarts
systemctl show bakici-server.service -p MainPID -p MemoryCurrent -p CPUUsageNSec -p NRestarts
free -m
sudo journalctl -u bakici-agent.service --since '30 minutes ago' --no-pager
```

Run the server line only where installed. If accounting reports `[not set]`, record process RSS and CPU using `ps` for the shown MainPID; do not invent a zero. Compare growth and report gaps over time, and check browser responsiveness. Remove one non-server node temporarily: the other eight and the GUI must remain available. Restore it and verify fresh scans. Configuration is persisted; observation history is intentionally not retained.

## Operations and rollback

```bash
sudo systemctl status bakici-agent.service --no-pager
sudo journalctl -u bakici-agent.service -n 100 --no-pager
sudo journalctl -u bakici-server.service -n 100 --no-pager
sudo systemctl restart bakici-agent.service
cat /opt/bakici-app/VERSION
```

Keep the previously accepted archive and its matching version marker, plus protected copies of `/etc/bakici` configs. Before an update, record that version and back up any config you will edit. Stop only the relevant units, save the old `/opt/bakici-app` directory under a versioned name, install the new archive into a new `/opt/bakici-app`, recreate the scanner venv if applicable, then start and inspect the units. Never unpack a new version on top of a running process.

To roll back, stop those units, move the failed new application directory aside, restore the previous application directory at `/opt/bakici-app`, restore a matching configuration only if its schema changed, and start the same units. Virtual-environment entry points keep their expected path when the original directory is restored. If unit files changed, restore their prior versions and run `systemctl daemon-reload`. Check known observations and current counts again. No observations need a database rollback.

When updating services, commands and config should remain reviewable. Do not claim the nine-Pi deployment complete while any required radio, reboot, network recovery or duration check remains untested.
