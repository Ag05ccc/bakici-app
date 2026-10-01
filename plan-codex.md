# Bluetooth Scanner — Implementation Plan

## Summary

Build a simple Python terminal app for this Ubuntu PC that discovers nearby Bluetooth Classic and Bluetooth Low Energy devices and lists all available discovery information.

Start with one script, one timed scan, and readable output. No connections, pairing, database, or graphical interface in the first version.

## Implementation

- Use Python 3.12 and `dbus-fast` to communicate directly with the existing BlueZ service.
- Use BlueZ's `Transport="auto"` discovery filter for Classic and BLE, as supported by the [BlueZ discovery API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Adapter.rst).
- Select the first powered adapter in adapter-name order.
- Subscribe to device events before starting discovery; collect results for 15 seconds by default.
- Merge repeated updates into one record per BlueZ device object.
- Use cached properties as context, but require discovery activity during the current scan before including a device. Old cached entries alone must not count as nearby discoveries.
- Always release the app's discovery session on completion, failure, or Ctrl+C.
- Provide clear errors for unavailable Bluetooth, missing adapters, and denied access.

Keep scanning, collection, and formatting in separate functions within `scan_bluetooth.py`. Include a dependency file and short setup instructions using a Python virtual environment.

## Commands and output

```bash
python scan_bluetooth.py
python scan_bluetooth.py --timeout 30
python scan_bluetooth.py --json > devices.json
```

Validate that the timeout is a positive, finite number.

Print a summary table sorted by strongest available signal, followed by each device's full available properties:

- Name, address, and address type.
- RSSI and transmit power.
- Service UUIDs, device class, appearance, and icon.
- Manufacturer data and service data.
- Paired and connected status.
- Other properties exposed by BlueZ.

These fields are device-dependent. Display missing values as "Unknown" and binary data as hexadecimal. See the [BlueZ device API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Device.rst).

JSON mode prints the same information as a JSON array to standard output; progress and errors go to standard error. An empty scan returns an empty array or "No devices discovered." Ctrl+C preserves collected results.

## Validation

- Discover a known BLE advertiser and a Classic device in discoverable mode.
- Verify repeated updates merge correctly and delayed names appear.
- Verify cached devices without current discovery activity are excluded.
- Check missing fields, binary formatting, and valid JSON output.
- Confirm timeout and Ctrl+C release discovery resources.
- Test missing adapters, Bluetooth being off, and permission errors using simulated backend responses.

## Limits and later additions

Discovery cannot guarantee every nearby device or reveal every device detail. Rotating addresses may make one physical device appear multiple times. Signal strength is not a reliable distance measurement.

After the first version works, add company and service names. Persistent scan history and device connections remain future work.

## Browser extension

The terminal scanner is now also used by a lightweight browser GUI for a headless Raspberry Pi 4:

- `web_app.py` uses Python's standard-library HTTP server and a single background scan worker.
- `static/` contains plain HTML, CSS, and JavaScript with no frontend packages or build step.
- The browser shows live discovery results, selectable device details, Start/Stop controls, and JSON export.
- `scan_bluetooth.scan()` provides optional device/status callbacks alongside the CLI.
- The Pi needs no desktop or browser. `dbus-fast` remains the only pip dependency.
- Raspberry Pi OS Lite 64-bit setup and LAN access are documented in `README.md`.
