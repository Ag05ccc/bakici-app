#!/usr/bin/env node
// Optional developer check: Node built-ins + installed Chrome, no npm packages.
// Starts only temporary local servers, posts synthetic data, never scans radios.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temporary = await mkdtemp(path.join(os.tmpdir(), "nearby-browser-"));
const children = [];
const runtimeErrors = [];
const browserRequests = [];
const checks = [];
let fixtureStopped = false;
let fixtureTask;
let fixtureFailure;
let chrome;
let commandId = 0;
const pending = new Map();

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function pass(name) { checks.push(name); console.log(`PASS ${name}`); }
async function until(fn, message, timeout = 12000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error(`Timed out: ${message}`);
}
async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
function child(executable, args, options = {}) {
  const process = spawn(executable, args, { cwd: root, stdio: ["ignore", "pipe", "pipe"], ...options });
  children.push(process);
  process.log = "";
  for (const stream of [process.stdout, process.stderr]) stream?.on("data", (chunk) => { process.log = (process.log + chunk).slice(-12000); });
  process.on("error", (error) => { process.launchError = error; });
  return process;
}
async function waitServer(server, url) {
  await until(async () => {
    if (server.launchError) throw server.launchError;
    if (server.exitCode !== null) throw new Error(`Server exited: ${server.log}`);
    try { return (await fetch(url)).ok; } catch { return false; }
  }, "temporary server startup");
}
function cdp(method, params = {}, sessionId) {
  const id = ++commandId;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timed out: ${method}`)); }, 12000);
    pending.set(id, { resolve, reject, timer });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0");
  });
}
function attachCDP() {
  let buffer = "";
  chrome.stdio[4].setEncoding("utf8");
  chrome.stdio[4].on("data", (chunk) => {
    buffer += chunk;
    let separator;
    while ((separator = buffer.indexOf("\0")) !== -1) {
      const raw = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 1);
      if (!raw) continue;
      const message = JSON.parse(raw);
      if (message.id && pending.has(message.id)) {
        const entry = pending.get(message.id);
        pending.delete(message.id);
        clearTimeout(entry.timer);
        if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
        else entry.resolve(message.result);
      } else if (message.method === "Runtime.exceptionThrown") {
        runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
      } else if (message.method === "Network.requestWillBeSent") {
        browserRequests.push(message.params.request.url);
      }
    }
  });
}
async function evaluate(session, expression) {
  const result = await cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, session);
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function click(session, selector) {
  const point = await evaluate(session, `(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) throw new Error("Missing element");
    element.scrollIntoView({ block: "center", inline: "center" });
    const bounds = element.getBoundingClientRect();
    const x = bounds.left + bounds.width / 2, y = bounds.top + bounds.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (!bounds.width || !bounds.height || !element.contains(hit)) throw new Error("Element is hidden or covered");
    return { x, y };
  })()`);
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", ...point, button: "left", clickCount: 1 }, session);
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", ...point, button: "left", clickCount: 1 }, session);
}
async function text(session, selector) { return evaluate(session, `document.querySelector(${JSON.stringify(selector)})?.textContent`); }
async function screenshot(session, filename) {
  const { cssContentSize } = await cdp("Page.getLayoutMetrics", {}, session);
  const result = await cdp("Page.captureScreenshot", {
    format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: cssContentSize.width, height: cssContentSize.height, scale: 1 },
  }, session);
  await writeFile(filename, Buffer.from(result.data, "base64"));
}

try {
  const config = JSON.parse(await readFile(path.join(root, "config/server.example.json"), "utf8"));
  config.freshness = { node: 5, bluetooth: 3, wifi: 5 };
  const configPath = path.join(temporary, "server.json");
  await writeFile(configPath, JSON.stringify(config));
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const server = child("python3", ["-S", "web_app.py", "--mode", "server", "--config", configPath, "--host", "127.0.0.1", "--port", String(port)]);
  await waitServer(server, base + "/api/dashboard");

  const malicious = '<img src=x onerror="window.__unsafe=1">';
  const sharedAddress = "02:00:00:00:00:01";
  const observations = (index, radio) => {
    const node = config.nodes[index - 1];
    if (radio === "bluetooth") {
      if (index === 3) return [];
      const result = [{ path: `/fixture/${node.id}/bt/shared`, properties: {
        Name: index === 1 ? malicious : "Shared beacon", Address: sharedAddress,
        AddressType: index === 9 ? "random" : "public", RSSI: -35 - index * 4,
        ManufacturerData: { "0x1234": "00ff12" }, Connected: false,
      } }];
      if (index === 1) result.push({ path: `/fixture/${node.id}/bt/local`, properties: { Name: "Sensor Ω", Address: "02:00:00:00:00:02", RSSI: -81 } });
      return result;
    }
    return [{ path: `/fixture/${node.id}/wifi/ap`, properties: {
      SSID: index === 1 ? malicious : "Shared network", SSIDHex: "536861726564206e6574776f726b",
      BSSID: index === 9 ? "02:00:00:00:01:02" : "02:00:00:00:01:01",
      Strength: 90 - index * 4, Frequency: 2412, Channel: 1, Security: "WPA2 Personal",
    } }];
  };
  async function post(index, report) {
    const node = config.nodes[index - 1];
    const response = await fetch(base + "/api/reports", {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${node.api_key}` },
      body: JSON.stringify({ node_id: node.id, ...report }),
    });
    if (!response.ok) throw new Error(`Fixture rejected: HTTP ${response.status} ${await response.text()}`);
  }
  async function send(index, initial = false) {
    if (index === 7 || (index === 6 && !initial)) return;
    await post(index, { kind: "heartbeat", enabled_radios: index === 8 ? ["bluetooth"] : ["bluetooth", "wifi"] });
    for (const radio of ["bluetooth", "wifi"]) {
      if ((index === 8 && radio === "wifi") || (index === 5 && radio === "bluetooth" && !initial)) continue;
      if (index === 4 && radio === "bluetooth" && !initial) await post(index, { kind: "scan", radio, status: "error", error: "Synthetic adapter failure" });
      else await post(index, { kind: "scan", radio, status: "success", observations: observations(index, radio) });
    }
  }
  for (let index = 1; index <= 9; index++) await send(index, true);
  fixtureTask = (async () => {
    while (!fixtureStopped) {
      for (let index = 1; index <= 9 && !fixtureStopped; index++) await send(index);
      await sleep(600);
    }
  })().catch((error) => { fixtureFailure = error; fixtureStopped = true; });

  chrome = child(process.env.CHROME_BIN || "/usr/bin/google-chrome", [
    "--headless", "--no-sandbox", "--disable-gpu", "--remote-debugging-pipe",
    "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    `--user-data-dir=${path.join(temporary, "chrome")}`, "about:blank",
  ], { stdio: ["ignore", "pipe", "pipe", "pipe", "pipe"] });
  attachCDP();
  const { targetId } = await cdp("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp("Target.attachToTarget", { targetId, flatten: true });
  await cdp("Runtime.enable", {}, sessionId);
  await cdp("Network.enable", {}, sessionId);
  await cdp("Page.enable", {}, sessionId);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);
  await cdp("Page.navigate", { url: base }, sessionId);
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('.node-marker').length === 9"), "nine configured markers");
  assert.deepEqual(await evaluate(sessionId, "Array.from(document.querySelectorAll('.floor')).map(floor => Number(floor.dataset.floor))"), [3, 2, 1]);
  pass("Nine Pi markers and floor order 3, 2, 1");
  await until(async () => await evaluate(sessionId, "document.querySelector('[data-node-id=\"pi-06\"]').dataset.state === 'offline' && document.querySelector('[data-node-id=\"pi-05\"]').dataset.state === 'stale'"), "offline and stale states", 15000);

  for (const radio of ["bluetooth", "wifi"]) {
    await click(sessionId, `#radio-${radio}`);
    const dashboard = await (await fetch(base + "/api/dashboard")).json();
    for (const node of dashboard.nodes) {
      await click(sessionId, `.node-marker[data-node-id="${node.id}"]`);
      assert.equal(await text(sessionId, "#scope-title"), node.label);
      assert.equal(await text(sessionId, "#node-location"), node.location);
      assert.equal(await text(sessionId, `.node-marker[data-node-id="${node.id}"] .node-count`), node.radios[radio].state === "fresh" ? String(node.radios[radio].count) : "—");
      assert.equal(await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length"), node.radios[radio].observations.length);
    }
    pass(`Every Pi selection, ${radio} counts, retained observations, and location match API`);
  }
  await click(sessionId, "#radio-bluetooth");
  const states = await evaluate(sessionId, "Object.fromEntries(Array.from(document.querySelectorAll('.node-marker')).map(marker => [marker.dataset.nodeId, [marker.dataset.state, marker.querySelector('.node-count').textContent]]))");
  assert.deepEqual(states["pi-03"], ["fresh", "0"]);
  assert.deepEqual(states["pi-04"], ["error", "—"]);
  assert.deepEqual(states["pi-05"], ["stale", "—"]);
  assert.deepEqual(states["pi-06"], ["offline", "—"]);
  assert.deepEqual(states["pi-07"], ["waiting", "—"]);
  await click(sessionId, "#radio-wifi");
  assert.equal(await text(sessionId, '[data-node-id="pi-08"] .node-state'), "Disabled");
  pass("Fresh zero, error, stale, offline, waiting, and disabled remain distinct");

  await click(sessionId, '[data-node-id="pi-01"]');
  assert((await text(sessionId, "#observations-body")).includes("86%"));
  assert((await text(sessionId, "#observations-body")).includes(malicious));
  assert.equal(await evaluate(sessionId, "document.querySelector('#observations-body img') === null && window.__unsafe === undefined"), true);
  await click(sessionId, ".observation-select");
  assert((await text(sessionId, "#properties")).includes("Strength (%)"));
  assert((await text(sessionId, "#properties")).includes("SSIDHex"));
  await click(sessionId, "#radio-bluetooth");
  assert((await text(sessionId, "#observations-body")).includes("-39 dBm"));
  assert((await text(sessionId, "#observations-body")).includes(malicious));
  await click(sessionId, ".observation-select");
  assert((await text(sessionId, "#properties")).includes("00ff12"));
  assert.equal(await evaluate(sessionId, "document.querySelector('#properties img') === null && window.__unsafe === undefined"), true);
  pass("Signal units, property selection, binary details, Unicode, and malicious names remain safe text");

  await click(sessionId, "#all-observations");
  const api = await (await fetch(base + "/api/dashboard")).json();
  assert.equal(await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length"), api.all_observations.bluetooth.length);
  const grouped = api.all_observations.bluetooth.find((group) => group.observations.length > 1);
  assert(grouped);
  const groupButton = await evaluate(sessionId, `Array.from(document.querySelectorAll('#observations-body tr')).findIndex(row => row.dataset.observationId === ${JSON.stringify(grouped.id)})`);
  await click(sessionId, `#observations-body tr:nth-child(${groupButton + 1}) button`);
  assert.equal(await evaluate(sessionId, "document.querySelectorAll('.reporter-detail').length"), grouped.observations.length);
  const groupedText = await text(sessionId, "#properties");
  for (const record of grouped.observations) assert(groupedText.includes(`${record.properties.RSSI} dBm`));
  pass("All observations group shared identity and preserve every reporting Pi's signal");

  await evaluate(sessionId, `(() => { const original = URL.createObjectURL; URL.createObjectURL = function(blob) { blob.text().then(text => { window.__export = JSON.parse(text); }); return original.call(this, blob); }; })()`);
  await cdp("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: temporary });
  await click(sessionId, "#export-button");
  await until(async () => await evaluate(sessionId, "Boolean(window.__export)"), "all observations export");
  let exported = await evaluate(sessionId, "window.__export");
  assert.equal(exported.scope.type, "all_observations");
  assert.equal(exported.radio, "bluetooth");
  assert.equal(exported.outdated, false);
  assert.equal(exported.observations.length, api.all_observations.bluetooth.length);
  assert(!JSON.stringify(exported).includes("api_key"));
  await click(sessionId, '[data-node-id="pi-04"]');
  await evaluate(sessionId, "window.__export = null");
  await click(sessionId, "#export-button");
  await until(async () => await evaluate(sessionId, "Boolean(window.__export)"), "node export");
  exported = await evaluate(sessionId, "window.__export");
  assert.equal(exported.scope.node_id, "pi-04");
  assert.equal(exported.nodes[0].radio_state, "error");
  assert.equal(exported.observations.length, 1);
  pass("All/node JSON exports match scope, retained data, health, and contain no keys");

  await click(sessionId, '[data-node-id="pi-01"]');
  await screenshot(sessionId, "/tmp/nearby-building-desktop.png");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
  await sleep(100);
  assert.equal(await evaluate(sessionId, "document.documentElement.scrollWidth <= innerWidth"), true);
  for (const node of config.nodes) await click(sessionId, `.node-marker[data-node-id="${node.id}"]`);
  await click(sessionId, '[data-node-id="pi-01"]');
  await screenshot(sessionId, "/tmp/nearby-building-mobile.png");
  pass("390px mobile layout has no horizontal overflow and all markers remain clickable");

  await cdp("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }, sessionId);
  await until(async () => await evaluate(sessionId, "!document.getElementById('connection-banner').hidden"), "disconnection banner");
  assert((await text(sessionId, "#connection-banner")).includes("outdated"));
  assert.equal(await text(sessionId, '[data-node-id="pi-01"] .node-count'), "—");
  assert.equal(await evaluate(sessionId, "document.querySelectorAll('.node-marker').length"), 9);
  await cdp("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }, sessionId);
  await until(async () => await evaluate(sessionId, "document.getElementById('connection-banner').hidden && document.querySelector('[data-node-id=\"pi-01\"] .node-count').textContent === '2'"), "automatic recovery");
  pass("Disconnected browser marks retained view outdated and automatically recovers");

  const localPort = await freePort();
  const localBase = `http://127.0.0.1:${localPort}`;
  const local = child(path.join(root, ".venv/bin/python"), ["web_app.py", "--mode", "local", "--host", "127.0.0.1", "--port", String(localPort)]);
  await waitServer(local, localBase + "/api/status");
  await cdp("Page.navigate", { url: localBase }, sessionId);
  await until(async () => await evaluate(sessionId, "document.getElementById('scan-button') && !document.getElementById('scan-button').disabled"), "existing local GUI ready");
  assert((await text(sessionId, "#status")).includes("Ready"));
  pass("Existing local Bluetooth GUI loads ready without starting a hardware scan");

  // The real ScanService/HTTP handler renders deterministic callback updates.
  // File gates let the browser inspect each phase without racing scan timers.
  const scanPort = await freePort();
  const scanBase = `http://127.0.0.1:${scanPort}`;
  const syntheticBackend = String.raw`
import asyncio
import copy
from pathlib import Path
import signal
import sys
import threading
from web_app import ScanService, ScannerHTTPServer

gates = Path(sys.argv[2])
attempt = 0

async def fake_scan(timeout, stop_event, *, on_update, on_status):
    global attempt
    attempt += 1
    number = attempt
    records = {}
    deadline = asyncio.get_running_loop().time() + timeout
    async def wait_gate(stage):
        while not (gates / f"scan-{number}-{stage}").exists():
            if stop_event.is_set() or asyncio.get_running_loop().time() >= deadline:
                return False
            try:
                await asyncio.wait_for(stop_event.wait(), 0.05)
            except TimeoutError:
                pass
        return not stop_event.is_set()
    def emit(record):
        records[record["path"]] = record
        on_update(copy.deepcopy(record))
    on_status(f"Synthetic scan {number}: waiting for observations")
    if await wait_gate("first"):
        path = f"/synthetic/scan_{number}/device_a"
        props = {"Address": "02:00:00:00:02:01", "AddressType": "public",
                 "RSSI": -70, "Paired": False, "Connected": False,
                 "ManufacturerData": {"0x1234": "00ff12"}}
        emit({"path": path, "properties": props})
        if await wait_gate("updated"):
            emit({"path": path, "properties": {**props, "Name": f"Delayed beacon {number}", "RSSI": -43}})
            emit({"path": f"/synthetic/scan_{number}/device_b", "properties": {
                "Name": f"Second beacon {number}", "Address": "02:00:00:00:02:02",
                "RSSI": -81, "Paired": False, "Connected": False}})
            await wait_gate("finish")
    return list(records.values())

service = ScanService(fake_scan)
server = ScannerHTTPServer(("127.0.0.1", int(sys.argv[1])), service)
def shutdown(signum, frame):
    threading.Thread(target=server.shutdown, daemon=True).start()
for signum in (signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, shutdown)
try:
    server.serve_forever()
finally:
    service.close()
    server.server_close()
`;
  const synthetic = child(path.join(root, ".venv/bin/python"), ["-c", syntheticBackend, String(scanPort), temporary]);
  await waitServer(synthetic, scanBase + "/api/status");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);
  await cdp("Page.navigate", { url: scanBase }, sessionId);
  await until(async () => await evaluate(sessionId, "document.getElementById('scan-button') && !document.getElementById('scan-button').disabled"), "synthetic local GUI ready");
  await evaluate(sessionId, "document.getElementById('duration').value = '120'");
  await click(sessionId, "#scan-button");
  await until(async () => await evaluate(sessionId, "!document.getElementById('stop-button').disabled && document.getElementById('scan-button').disabled"), "local scan started");
  assert.equal(await text(sessionId, "#device-count"), "0");
  await writeFile(path.join(temporary, "scan-1-first"), "");
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('#devices tr').length === 1"), "first streaming observation");
  assert.equal(await text(sessionId, ".device-select"), "Unnamed device");
  assert((await text(sessionId, "#devices")).includes("-70 dBm"));
  await click(sessionId, ".device-select");
  assert((await text(sessionId, "#properties")).includes("00ff12"));
  await writeFile(path.join(temporary, "scan-1-updated"), "");
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('#devices tr').length === 2 && document.getElementById('device-name').textContent === 'Delayed beacon 1'"), "merged delayed name and second device");
  assert((await text(sessionId, "#devices")).includes("-43 dBm"));
  assert.equal(await evaluate(sessionId, "document.querySelectorAll('#devices tr[data-path=\"/synthetic/scan_1/device_a\"]').length"), 1);
  assert.equal((await (await fetch(scanBase + "/api/status")).json()).scanning, true);
  pass("Local Start streams results, merges repeated updates, and refreshes selected delayed names/details");

  const { targetId: secondTarget } = await cdp("Target.createTarget", { url: "about:blank" });
  const { sessionId: secondSession } = await cdp("Target.attachToTarget", { targetId: secondTarget, flatten: true });
  await cdp("Runtime.enable", {}, secondSession);
  await cdp("Network.enable", {}, secondSession);
  await cdp("Page.enable", {}, secondSession);
  await cdp("Page.navigate", { url: scanBase }, secondSession);
  await until(async () => await evaluate(secondSession, "document.querySelectorAll('#devices tr').length === 2 && document.getElementById('scan-button').disabled && !document.getElementById('stop-button').disabled"), "second tab joins shared scan");
  assert.equal(await evaluate(sessionId, "document.getElementById('scan-button').disabled"), true);
  assert.equal(await evaluate(secondSession, `fetch('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ timeout: 120 }) }).then(response => response.status)`), 409);
  const sharedScan = await (await fetch(scanBase + "/api/status")).json();
  assert.equal(sharedScan.scanning, true);
  assert.equal(sharedScan.devices.length, 2);
  assert(sharedScan.devices.every((record) => record.path.startsWith("/synthetic/scan_1/")));
  await click(secondSession, '#devices tr[data-path="/synthetic/scan_1/device_b"] button');
  assert.equal(await text(secondSession, "#device-name"), "Second beacon 1");
  pass("Two local GUI tabs share one scan, block duplicate Start, and select device details independently");

  await click(secondSession, "#stop-button");
  await until(async () => await evaluate(secondSession, "!document.getElementById('scan-button').disabled && document.getElementById('stop-button').disabled"), "local scan stopped");
  await until(async () => await evaluate(sessionId, "!document.getElementById('scan-button').disabled"), "first tab observes Stop");
  assert.equal(await text(secondSession, "#status"), "Scan stopped.");
  assert.equal(await text(secondSession, "#device-count"), "2");
  const stoppedScan = await (await fetch(scanBase + "/api/status")).json();
  assert.equal(stoppedScan.scanning, false);
  assert.deepEqual(stoppedScan.devices, sharedScan.devices);
  await evaluate(secondSession, `(() => { const original = URL.createObjectURL; URL.createObjectURL = function(blob) { blob.text().then(text => { window.__export = JSON.parse(text); }); return original.call(this, blob); }; })()`);
  await click(secondSession, "#export-button");
  await until(async () => await evaluate(secondSession, "Array.isArray(window.__export)"), "local partial-results export");
  assert.deepEqual(await evaluate(secondSession, "window.__export"), stoppedScan.devices);
  pass("Local Stop retains partial results in both tabs and exports matching JSON");

  await click(secondSession, "#scan-button");
  await until(async () => await evaluate(secondSession, "document.getElementById('scan-button').disabled && !document.getElementById('stop-button').disabled && document.querySelectorAll('#devices tr').length === 0"), "restart clears prior scan");
  assert.equal(await text(secondSession, "#device-count"), "0");
  assert.equal(await evaluate(secondSession, "document.getElementById('export-button').disabled"), true);
  assert.equal(await evaluate(secondSession, "document.getElementById('details-content').hidden"), true);
  const restarted = await (await fetch(scanBase + "/api/status")).json();
  assert.equal(restarted.scanning, true);
  assert.deepEqual(restarted.devices, []);
  await writeFile(path.join(temporary, "scan-2-first"), "");
  await until(async () => await evaluate(secondSession, "document.querySelectorAll('#devices tr').length === 1"), "new scan streams fresh result");
  assert.equal(await evaluate(secondSession, "document.querySelector('#devices tr').dataset.path"), "/synthetic/scan_2/device_a");
  await writeFile(path.join(temporary, "scan-2-updated"), "");
  await until(async () => await evaluate(secondSession, "document.querySelectorAll('#devices tr').length === 2 && document.getElementById('devices').textContent.includes('Delayed beacon 2')"), "new scan receives delayed updates");
  await click(secondSession, "#stop-button");
  await until(async () => !(await (await fetch(scanBase + "/api/status")).json()).scanning, "second scan stopped");
  pass("Local restart clears old rows/details/export and streams only the new scan's updates");

  assert.deepEqual(runtimeErrors, []);
  if (fixtureFailure) throw fixtureFailure;
  assert(browserRequests.filter((url) => /^https?:/.test(url)).every((url) => [base, localBase, scanBase].some((origin) => url.startsWith(origin + "/"))));
  pass("No JavaScript runtime errors or external asset requests");
  console.log(`\n${checks.length} browser checks passed. Screenshots: /tmp/nearby-building-desktop.png and /tmp/nearby-building-mobile.png`);
} finally {
  fixtureStopped = true;
  if (fixtureTask) await fixtureTask.catch(() => {});
  for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(new Error("Browser shutting down")); }
  pending.clear();
  for (const process of children.reverse()) {
    if (process.exitCode !== null) continue;
    process.kill("SIGTERM");
    await Promise.race([new Promise((resolve) => process.once("exit", resolve)), sleep(1500)]);
    if (process.exitCode === null) process.kill("SIGKILL");
  }
  await rm(temporary, { recursive: true, force: true });
}
