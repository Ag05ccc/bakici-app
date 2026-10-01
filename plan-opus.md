# Bluetooth Scanner — Plan

**Goal:** a small app for this PC that finds Bluetooth devices nearby and shows everything it can learn about them.

**Rule:** start with the smallest thing that works. Each phase adds one thing. Phase 1 alone is already a working app.

## Tech choice

| What | Choice | Why |
|---|---|---|
| Language | Python 3.12 (already installed) | Short code, easy to change |
| Bluetooth | [bleak](https://github.com/hbldh/bleak) | Simple async API. On Linux it talks to BlueZ (the system Bluetooth service), so no `sudo` is needed |
| Interface | Command line first | Fastest way to see results. A real UI comes in Phase 6 |

This PC: Ubuntu 24.04, BlueZ 5.72, adapter `hci0` (Intel, Bluetooth 5.3, powered on, supports Low Energy and Classic).

## Bluetooth in 1 minute (what we can and cannot see)

- **Low Energy (BLE):** phones, watches, earbuds, trackers, sensors, TVs, laptops. They send small "advertisement" packets all the time. Most devices around you are BLE.
- **Classic:** used for audio (speakers, headsets, car kits) and older devices. We can see them only while they are in pairing mode.
- **Scanning = listening.** We read advertisements. No connection, no pairing.
- **One advertisement can contain:** address (MAC), name (often missing), signal strength (RSSI), TX power, manufacturer data (company ID + bytes), service UUIDs, service data.
- **Random addresses:** phones change their address about every 15 minutes for privacy. One phone can appear as several devices, and a random address does not tell us the vendor.
- **More details need a connection:** model, firmware and battery live inside the device (GATT). We only do this with our own devices.

## Step 0 — Setup (5 minutes)

```bash
cd /home/gz/bakici-app
/usr/bin/python3 -m venv .venv      # own venv (the shell has /home/gz/VBN/.venv active)
source .venv/bin/activate
pip install bleak
pip freeze > requirements.txt
```

Check that Bluetooth works without our code: `bluetoothctl --timeout 10 scan on` should print `[NEW] Device ...` lines.

## Phase 1 — MVP: "What is around me?"

**Goal:** one command prints a list of nearby devices with all their raw data.

One file, `scan.py`:

```python
import asyncio
from bleak import BleakScanner

async def main():
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    by_signal = sorted(found.values(), key=lambda item: item[1].rssi, reverse=True)
    for device, adv in by_signal:
        print(f"{device.address}  {adv.rssi} dBm  {adv.local_name or '(no name)'}")
        print("   tx power:    ", adv.tx_power)
        print("   manufacturer:", {hex(cid): data.hex() for cid, data in adv.manufacturer_data.items()})
        print("   services:    ", adv.service_uuids)
        print("   service data:", {uuid: data.hex() for uuid, data in adv.service_data.items()})
    print(f"\n{len(found)} devices found")

asyncio.run(main())
```

**Done when:**
- `python scan.py` runs for about 10 seconds and prints the list.
- Your phone, held next to the PC, is at or near the top (strongest signal).
- The device count is close to what `bluetoothctl` found.

## Phase 2 — Make it readable

**Goal:** turn numbers and hex into words.

- **Company name** from the manufacturer ID: `0x004C` → Apple, `0x0006` → Microsoft, `0x0075` → Samsung. Use the [`bluetooth-numbers`](https://pypi.org/project/bluetooth-numbers/) package.
- **Service names** from UUIDs: `0x180F` → Battery, `0x180D` → Heart Rate (same package).
- **Address type:** public (fixed, set by the maker) or random. Random can be static (fixed until reset) or private (changes over time). BlueZ says public or random; the top 2 bits of the first byte tell which random kind.
- **Vendor from MAC address** (OUI lookup, same package). Only works for public addresses.
- **Linux extras from BlueZ** (bleak passes them through in `device.details`): appearance (`0x00C0` → Watch, `0x03C1` → Keyboard), icon, advertising flags, paired, connected.
- **Rough distance** from RSSI: near / medium / far. It is only a hint.
- **Nice table** with [`rich`](https://github.com/Textualize/rich).
- **Options:** `--time 20` (scan length), `--json devices.json` (save everything).
- **Friendly error** if Bluetooth is off or there is no adapter.
- Put the decode functions in `decode.py`. Test them with `pytest` and known sample values.
- New packages: `pip install rich bluetooth-numbers pytest`, then update `requirements.txt`.

**Done when:** the table is easy to read, `pytest` passes, and the JSON file opens.

## Phase 3 — Live mode + Classic devices

**Goal:** keep scanning, and see Classic devices too.

- Scan without stopping. Update the table every second (`BleakScanner` with a `detection_callback` + `rich.live`).
- For each device keep: first seen, last seen, number of adverts, RSSI now / min / max. Mark a device "gone" after 30 seconds without adverts.
- **Classic devices (Linux):** ask BlueZ to scan both types (discovery filter `Transport = "auto"`, passed with bleak's `bluez=` scanner option).
- Decode **Class of Device** into a type: `0x5A020C` → Phone / Smartphone. This PC's own adapter reports `0x6C0104` → Computer / Desktop (a good test value).
- A `--live` flag turns this mode on. The simple one-shot scan stays the default.

**Done when:** moving your phone away lowers its RSSI; turning its Bluetooth off marks it "gone"; a headset or speaker in pairing mode appears with its type.

## Phase 4 — Recognize devices from their data

**Goal:** name the device even when it sends no name. Most devices send no name, but their data says a lot.

- **Apple** (`0x004C`): message type → iPhone / iPad / Mac ("Nearby Info"), AirPods ("Proximity Pairing"), AirTag / Find My, iBeacon (UUID, major, minor).
- **Microsoft** (`0x0006`): Windows PC, Swift Pair accessory.
- **Google Fast Pair** (service `0xFE2C`): model ID.
- **Eddystone beacons** (service `0xFEAA`): UID / URL / telemetry.
- Add a **"Looks like"** column, for example "Apple – AirPods".
- One small function per format in `recognize.py`, each with a test that uses sample bytes.

**Done when:** your own devices (phone, earbuds, laptop) get a correct guess.

## Phase 5 — Deep info by connecting (your own devices only)

**Goal:** read what the device knows about itself.

- `python scan.py --connect AA:BB:CC:DD:EE:FF`
- Connect with `BleakClient`. List all services and characteristics (with read / write / notify flags).
- Read standard values: device name, appearance, Device Information (manufacturer, model, serial, firmware / hardware / software version), battery level.
- Use a timeout and clear error messages. Many devices refuse connections or need pairing.
- Code goes in `gatt_info.py`.

**Done when:** a BLE device you own (watch, earbuds, mouse) shows its model, firmware and battery, and the battery matches what your phone shows. A device that refuses gives a clear error, not a crash.

## Phase 6 (optional) — Real app UI

**Goal:** use everything without typing commands.

- Terminal app with [Textual](https://textual.textualize.io/) (it uses asyncio, like bleak).
- Live device list. Select a device to see all its details.
- Keys: `s` start/stop scan, `c` connect (deep info), `e` export JSON.
- Code goes in `app.py`.

**Done when:** all Phase 1–5 features work from the UI.

## Where the code ends up

```
bakici-app/
├── plan.md
├── requirements.txt
├── scan.py          # command line entry (Phase 1+)
├── decode.py        # names, address type, appearance, class of device (Phase 2–3)
├── recognize.py     # Apple / Microsoft / Google / beacon formats (Phase 4)
├── gatt_info.py     # connect and read device info (Phase 5)
├── app.py           # Textual UI (Phase 6)
└── tests/           # pytest for decode.py and recognize.py
```

## Limits to expect

- Many devices show "(no name)". This is normal.
- One phone can appear many times (its random address changes).
- Distance from RSSI is rough. Walls, bodies and phone position change it a lot.
- Devices connected to a phone or PC (for example headphones in use) often stop advertising.

## Ideas for later

- Save history in SQLite (what was around, and when).
- Filters: minimum RSSI, name, company.
- Show devices already paired or connected to this PC (from BlueZ).
- Service list (SDP) for Classic devices.
- Try on Windows / macOS. The core works there; the Linux extras need checks (macOS hides MAC addresses).

## Privacy

Scanning only listens to public broadcasts. Do not use it to track people. Connect only to your own devices.
