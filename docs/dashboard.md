# Try the building dashboard

The building view is served by `web_app.py --mode server`. It contains three floor panels with configured Pi markers; the radio selector controls marker counts and the result list. Clicking a floor or a Pi narrows the list; selecting a result shows its identity, each reporting Pi's measurement and every available property. All observations groups shared addresses while keeping each Pi's signal reading.

The interface text is Turkish; button names below are given as shown on screen. The frontend is plain HTML/CSS/JavaScript with an SVG floor background, system fonts, no build step, and no external requests. It polls every 2 seconds (10 seconds when hidden). The Pi runs only the Python server; open the browser on your PC.

## Using the dashboard

- **Layout.** On wide screens the building map, result list and observation details sit side by side; the details column stays in view while the list scrolls. Narrower screens stack them, and below 1100px the map starts as a compact floor-by-floor overview (**Planı göster** shows configured positions). The navy top bar holds the radio switch, health counts, the server connection and the theme switch.
- **Scope.** Click a floor name for that floor's Pis or a marker for one Pi. The breadcrumb (`Building › Floor 3 › Pi 08`) returns to the floor or the building; its × clears the Pi selection. A floor scope removes other floors' reporters before searching, counting and exporting.
- **Search and sort.** Search is a trimmed, case-insensitive text match on any in-scope Pi's reported name or address. Sorting happens when you change the search, scope or sort, or press **Şimdi sırala** (sort now). While polling, existing rows keep their place, new rows join the end with a **Yeni** (new) tag, and a `3 yeni · Şimdi sırala` button reorders them. Pi badges always show each Pi's own latest scan count.
- **Linked map.** Selecting (or hovering) a result outlines the Pis that reported it and shows each Pi's own signal under its marker, marked `old` for a previous result. It shows which scanners heard the device, not where the device is.
- **Health.** The **Pi çevrimiçi** (Pis online) button opens a list of offline Pis and enabled radios that are waiting, stale or in error; selecting an entry opens that Pi and radio. Disabled radios are listed separately and are not failures. When a Pi's selected radio is not fresh, an amber banner explains that its rows are previous results.
- **Details.** Identity fields reported identically by every in-scope Pi are shown once; any that differ or are missing are listed per Pi. Signal, scan-report age and state are shown per Pi, and each report's full original properties and object path stay in an expandable section.
- **Links and keys.** Radio, floor, Pi, search, sort and the selected result are kept in the address bar, so refresh, bookmarks and Back keep the view; invalid or removed values fall back to the default view. Keys: `/` search, `↑`/`↓` move between rows when a row has focus, `Esc` clears the search or selection, `B`/`W` switch radio. Shortcuts are ignored while typing.
- **Theme.** Automatic follows the system's light/dark setting; the top-bar switch cycles light, dark and automatic, remembered in that browser only.

## Nine simulated nodes, no radio hardware needed

```bash
cp config/server.example.json config/server.local.json
python3 web_app.py --mode server --config config/server.local.json --host 127.0.0.1 --port 8001
```

In another terminal:

```bash
python3 tools/demo_reports.py --config config/server.local.json --scenario nine-nodes \
  --error-radio pi-08:wifi --empty-radio pi-09:bluetooth
```

Open `http://127.0.0.1:8001/`. The helper sends synthetic fixtures every 10 seconds. `pi-08` shows a Wi-Fi error with retained results; `pi-09` has a fresh Bluetooth count of zero. Shared addresses demonstrate grouping; shared SSIDs with different BSSIDs remain separate access points. Add `--cycles 2` for a bounded demonstration, or Ctrl+C to stop it.

To show one Pi going offline, stop the helper and restart with the same options plus `--pause-node pi-03`. Within 60 seconds of its last report, that marker goes offline while others keep updating. Restart without `--pause-node` to restore it. For repeated errors on another radio use `--error-radio pi-02:bluetooth`; `--empty-radio` means a successful empty scan instead of an error.

These are development fixtures, not physical Pi observations. Do not run this helper with a live production registry: sample reports would replace that node's latest results. Use a separate demo server/configuration and the obvious example keys.

## Reading the view

- **Fresh, count 0:** a successful scan found nothing during its observation window.
- **Waiting:** the server has not received a result yet.
- **Stale or scan error:** previous observations may be retained but are excluded from current counts.
- **Disabled:** that radio is disabled in the agent configuration.
- **Offline:** the node has stopped reporting. Other nodes remain independent.
- **Server unavailable:** the last browser view is labelled outdated, counts are unavailable, and polling retries automatically.

Bluetooth signal is dBm; Wi-Fi signal quality is a percentage. Pi positions are configured locations, not detected device positions. The All observations view excludes stale results and never combines Bluetooth/Wi-Fi into a physical-device total.

**Dışa aktar → JSON** saves exactly the rows shown, in their displayed order, with the radio, scope, search/sort settings (`filters`), `counts` (shown and in scope), reporting nodes and freshness context. A filtered view exports only its matching rows; an empty filtered export is a valid empty list with its context. A node export may include retained stale results and labels them accordingly. An export made while disconnected records `outdated: true`.

**Dışa aktar → CSV** writes one line per reporting Pi and displayed result, with the same scope/search/freshness columns and each report's original properties as JSON. Device-supplied text that a spreadsheet would treat as a formula (`=`, `+`, `-`, `@`) is prefixed with `'`.

For remote access bind the server to `0.0.0.0` and browse to its actual IP, for example `http://192.168.1.50:8001/`. Keep this initial service on the trusted local network. Its dashboard is visible to anyone who can reach the port; node reports require their configured keys.
