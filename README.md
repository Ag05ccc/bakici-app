# Bluetooth scanner

A small Bluetooth scanner with a browser GUI and a reusable Python backend. It discovers Bluetooth Classic and BLE devices through Linux BlueZ, without connecting or pairing. The browser can run on another computer or phone while the scanner runs on a headless Raspberry Pi 4.

- **Backend:** `scan_bluetooth.py` handles Bluetooth; `web_app.py` serves the page and a small JSON API using Python's built-in `http.server`.
- **Frontend:** plain HTML, CSS, and JavaScript in `static/`. No build step, external fonts, CDN, or JavaScript packages. The three frontend files total about 21 KB uncompressed.
- **Dependencies:** Python 3.11+, the system BlueZ service, and **one pip package: `dbus-fast`**. The GUI introduces no additional pip dependencies.

The app includes a Bluetooth GUI and separate Bluetooth/Wi-Fi terminal scanners. The multi-Pi design is described in [system-plan.md](system-plan.md). Follow [development-plan.md](development-plan.md) and matching checks in [test-plan.md](test-plan.md); completed stages and pending hardware checks are recorded in [development-progress.md](development-progress.md).

## Raspberry Pi setup

Use **64-bit Raspberry Pi OS Lite, Bookworm or newer**. A desktop environment and browser are not needed on the Pi. The pinned `dbus-fast` release has prebuilt ARM64 wheels for the Python versions shipped with Bookworm and Trixie; 64-bit avoids needing a compiler for that dependency. A 32-bit OS is not the recommended minimal-install path.

Copy this project to the Pi, including the `static/` directory. Create a new virtual environment on the Pi; do not copy the Ubuntu `.venv` directory across machines.

```bash
# System packages; already-installed packages will be kept.
sudo apt-get update
sudo apt-get install --no-install-recommends python3-venv bluez

cd ~/bakici-app  # or the folder where you copied this project
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt

# Make the page accessible from another device on your network.
.venv/bin/python web_app.py --host 0.0.0.0
```

Find the Pi's address with `hostname -I`, then open **`http://<Pi-IP>:8000`** on your computer or phone. For example, if the Pi's address is `192.168.1.50`, open `http://192.168.1.50:8000`.

If Bluetooth is disabled, enable the service with `sudo systemctl enable --now bluetooth` and the adapter with `bluetoothctl power on`. The app itself runs as your normal user.

The server is a local/LAN tool with no login: anyone who can reach its port can see results and control scans. Keep that port on your private network. By default, without `--host 0.0.0.0`, it listens only on `127.0.0.1`.

## Setup on this Ubuntu PC

```bash
cd /home/gz/bakici-app
/usr/bin/python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If virtual-environment creation fails because `venv` is missing, install Ubuntu's `python3-venv` package. Enable Bluetooth in Ubuntu's settings before running the app. Normal desktop BlueZ permissions should be sufficient; the app does not require running the entire script as root or change adapter settings.

## Run the GUI

```bash
.venv/bin/python web_app.py
# Open http://127.0.0.1:8000

# Optional custom port / network access:
.venv/bin/python web_app.py --host 0.0.0.0 --port 8080
```

Choose a scan duration (1–300 seconds) and click **Start scan**. Devices appear as they are discovered, sorted by signal strength. Select a row to inspect all available properties. **Stop** ends the scan and keeps partial results; **Export JSON** downloads the displayed records.

Only one scan runs at a time, shared across browser tabs. Each new scan clears the previous list; refreshing the page reconnects to the current scan. Errors appear in the page and preserve any results already observed. Closing a browser tab does not stop discovery; the scan finishes at its timeout. Ctrl+C or SIGTERM on the server requests a stop and waits for Bluetooth cleanup.

The page polls once per second (every five seconds while its tab is hidden), redraws device data only when it changes, and requires no Internet connection after setup. Results stay in memory unless you export them.

## Run the CLI

```bash
python scan_bluetooth.py
python scan_bluetooth.py --timeout 30
python scan_bluetooth.py --json > devices.json
```

The default scan lasts 15 seconds. The first powered adapter in adapter-name order is used. The timeout accepts any positive, finite number of seconds, including fractions. Ctrl+C ends discovery early and prints collected results.

Text output includes a table sorted by strongest available RSSI, then all exposed `org.bluez.Device1` properties for each observed device. Missing standard fields appear as `Unknown`. Signal strength is measured in dBm; a less negative value indicates a stronger received signal, not a known distance.

JSON output is an array of objects with `path` (the BlueZ object path) and `properties` (the device properties). Missing standard fields are `null`; booleans and numbers retain their types. Byte arrays are lowercase hexadecimal strings, and numeric dictionary keys such as manufacturer IDs are hexadecimal strings (`0x004c`). Progress and errors go to stderr so stdout contains only JSON. An empty scan returns `[]`.

Exit codes: `0` for a completed scan (including no devices), `1` for a Bluetooth failure, `2` for invalid arguments, and `130` for Ctrl+C. On failure, check stderr; stdout may be empty.

## Run the Wi-Fi scanner

Wi-Fi discovery uses the OS NetworkManager service and the same `dbus-fast` dependency. It lists nearby access points, not devices connected to them. It does not change network connections or enable monitor mode.

```bash
.venv/bin/python scan_wifi.py --timeout 15
.venv/bin/python scan_wifi.py --timeout 15 --json
.venv/bin/python scan_wifi.py --interface wlan0 --json
```

If no interface is specified, the first managed Wi-Fi interface in name order is used. The positive finite timeout bounds the whole operation. The scanner waits for completion and excludes cached access points outside this scan's observation window. Ctrl+C keeps fresh results already read and releases its D-Bus connection.

Records use `{path, properties}`. `BSSID` identifies an AP; `SSID` is display text and `SSIDHex` preserves the original bytes, also retained as `Ssid`. `Strength` is quality **in percent**, not dBm; `Frequency` is MHz; unknown channels are `null`. `Security` describes advertised authentication. `MaxBitrate` is advertised capability in Kb/s, not measured throughput. Original exposed properties remain available. JSON goes to stdout; status/errors go to stderr. Exit codes follow the Bluetooth CLI (0 success, 1 failure, 2 arguments, 130 interrupted).

The scan requires NetworkManager Wi-Fi scan permission for the running user. Test this under the service account on a Pi; a desktop user's successful scan does not prove headless service permissions. See the [NetworkManager wireless API](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.Device.Wireless.html) and [access-point properties](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.AccessPoint.html).

## Bluetooth discovery limits

- Classic devices must be discoverable; BLE devices must be advertising. Put a known Classic headset into its discoverable/pairing mode to test discovery, without pairing through this app.
- Devices already stored by BlueZ are included only after a new device object or discovery-related property update occurs during this scan. A name, pairing, or connection-status change alone does not count as a fresh sighting.
- Results are observations during the scan, not proof that a device is still nearby. They retain the last available properties even if BlueZ later removes the object. Some properties come from the OS cache and may be older than the scan.
- Repeated updates merge per BlueZ object. Rotating private addresses can still make one physical device appear more than once. Missing names are normal.
- The app shows properties exposed by BlueZ, not a capture of every radio packet. Model, battery, firmware, and serial numbers are not guaranteed. Raw manufacturer/service data is not decoded.
- Discovery may transmit inquiry/scan requests, but this app does not initiate connections or pairing. Other applications may run their own scans; ending this app releases only its own discovery session.

## Troubleshooting

Check `systemctl status bluetooth` for a stopped service, Ubuntu's Bluetooth settings for a disabled adapter, and `rfkill list bluetooth` for a blocked radio. If access is denied, check your user's D-Bus/BlueZ policy. The app reports errors rather than changing system settings.

## Test

```bash
python -m unittest discover -s tests -v
```

The automated tests use simulated D-Bus responses and need no Bluetooth hardware. For a hardware check, scan with a known BLE advertiser and a known discoverable Classic device nearby. Confirm both addresses appear, repeat with `--json`, and interrupt a longer scan with Ctrl+C. A nearby phone is not a dependable test device unless it is known to be advertising.

The web tests also cover live updates, duplicate scan prevention, Stop during startup, partial results on failure, HTTP validation, and server shutdown. To check the UI manually, start the web server, scan, select a row, export JSON, stop early, and repeat from a narrow browser window.

## Backend interface

The CLI and web server use the same asynchronous function:

```python
import scan_bluetooth

await scan_bluetooth.scan(
    15,
    stop_event,                 # asyncio.Event; set it to stop early
    on_update=receive_device,   # one {"path": ..., "properties": ...} record
    on_status=receive_status,   # a status string
)
```

Callbacks are optional, synchronous, and run on the scan's event-loop thread. They should return quickly. `scan()` returns the final list after releasing discovery and raises `ScanError` for expected failures. The backend does not print status itself; the CLI and web server handle presentation separately.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Current scan state and device records |
| `POST /api/scan` | Start with JSON `{"timeout": 15}`; returns 202, or 409 if busy |
| `POST /api/stop` | Stop with JSON `{}` and retain partial results |

Status includes `scanning`, `stopping`, `message`, `error`, `timeout`, `elapsed`, and `devices`. POST requests must use `Content-Type: application/json`. The browser and API are served from the same address.

Implementation references: [BlueZ adapter API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Adapter.rst), [BlueZ device API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Device.rst), and [dbus-fast](https://dbus-fast.readthedocs.io/en/latest/).
