# Try the building dashboard

The building view is served by `web_app.py --mode server`. It contains three floor panels with configured Pi markers; the radio selector controls marker counts and the result list. Clicking a Pi shows its location, last reporting IP, both radio states, and observations. Selecting a result reveals every available property. All observations groups shared addresses while keeping each Pi's signal reading.

The frontend is plain HTML/CSS/JavaScript with an SVG floor background, no build step, and no external requests. It polls every 2 seconds (10 seconds when hidden). The Pi runs only the Python server; open the browser on your PC.

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

Export view saves the selected radio and scope, observations, reporting nodes, and freshness context as JSON. A node export may include retained stale results and labels them accordingly. An export made while disconnected explicitly records that the browser's view is outdated.

For remote access bind the server to `0.0.0.0` and browse to its actual IP, for example `http://192.168.1.50:8001/`. Keep this initial service on the trusted local network. Its dashboard is visible to anyone who can reach the port; node reports require their configured keys.
