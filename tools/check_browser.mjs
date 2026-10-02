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
    if (!bounds.width || !bounds.height || !element.contains(hit)) throw new Error("Element is hidden or covered by " + (hit ? hit.tagName.toLowerCase() + (hit.className ? "." + hit.className : "") : "nothing"));
    return { x, y };
  })()`);
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", ...point, button: "left", clickCount: 1 }, session);
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", ...point, button: "left", clickCount: 1 }, session);
}
async function text(session, selector) { return evaluate(session, `document.querySelector(${JSON.stringify(selector)})?.textContent`); }
// Like until(), but tolerates evaluation errors while a navigation replaces the page.
async function untilPage(session, expression, message) {
  await until(async () => { try { return await evaluate(session, expression); } catch { return false; } }, message);
}
async function setSearch(session, value) {
  await evaluate(session, `(() => { const input = document.getElementById("search"); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event("input", { bubbles: true })); })()`);
}
async function key(session, name, code, keyCode, typed) {
  const base = { key: name, code, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode };
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", ...base, ...(typed ? { text: typed, unmodifiedText: typed } : {}) }, session);
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", ...base }, session);
}
// Visible text set below the 12px minimum, as "class:size" entries.
async function smallText(session) {
  return evaluate(session, `(() => {
    const small = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const parent = walker.currentNode.parentElement;
      if (!walker.currentNode.textContent.trim() || !parent || parent.closest("[hidden]") || !parent.getClientRects().length) continue;
      const size = parseFloat(getComputedStyle(parent).fontSize);
      if (size < 12) small.push(parent.className + ":" + size);
    }
    return small;
  })()`);
}
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
      if (index === 2) result.push({ path: `/fixture/${node.id}/bt/formula`, properties: { Name: "=1+2", Address: "02:00:00:00:00:03", RSSI: -77 } });
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
  assert.deepEqual(await smallText(sessionId), []);
  assert.equal(await evaluate(sessionId, "document.querySelectorAll('#legend li').length"), 6);
  assert.deepEqual(await evaluate(sessionId, "[getComputedStyle(document.getElementById('topbar')).position, document.getElementById('topbar').offsetHeight < 100]"), ["sticky", true]);
  pass("Readable text: nothing visible below 12px, six labelled legend states, one-row pinned top bar");
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
  assert.equal(await text(sessionId, '[data-node-id="pi-08"] .node-state'), "Devre dışı");
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

  await click(sessionId, "#scope-building");
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
  const groupedNodes = new Set(grouped.observations.map((record) => record.node_id));
  assert((await evaluate(sessionId, `document.querySelector('#observations-body tr:nth-child(${groupButton + 1})').lastElementChild.textContent`)).includes(`${groupedNodes.size} Pi · en güçlü:`));
  assert.deepEqual((await evaluate(sessionId, "Array.from(document.querySelectorAll('.node-marker.reports')).map(marker => marker.dataset.nodeId)")).sort(), [...groupedNodes].sort());
  for (const nodeId of groupedNodes) assert((await text(sessionId, `[data-node-id="${nodeId}"] .node-signal`)).endsWith("dBm"));
  assert.equal(await evaluate(sessionId, "Array.from(document.querySelectorAll('.node-marker:not(.reports)')).every(marker => marker.classList.contains('quiet'))"), true);
  assert.equal(await evaluate(sessionId, "Array.from(document.querySelectorAll('.node-count')).some(count => count.textContent.includes('dBm'))"), false);
  assert(groupedText.includes(`${grouped.observations.length} raporun tümünde aynı`));
  assert(groupedText.includes("Raporlar arasında farklı"));
  pass("Shared rows count distinct Pis, details compare reports, and the map outlines reporting Pis without changing counts");

  await evaluate(sessionId, `(() => { const original = URL.createObjectURL; URL.createObjectURL = function(blob) { blob.text().then(text => { if (blob.type.startsWith("text/csv")) window.__csv = text; else window.__export = JSON.parse(text); }); return original.call(this, blob); }; })()`);
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

  await click(sessionId, "#scope-building");
  await setSearch(sessionId, "  SENSOR  ");
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length === 1"), "search narrows the list");
  assert((await text(sessionId, "#observations-body")).includes("Sensor Ω"));
  assert.match(await text(sessionId, "#results-summary"), /^Gösterilen: 1 \/ \d+ gözlem$/);
  await evaluate(sessionId, "window.__export = null");
  await click(sessionId, "#export-button");
  await until(async () => await evaluate(sessionId, "Boolean(window.__export)"), "filtered export");
  exported = await evaluate(sessionId, "window.__export");
  assert.deepEqual([exported.filters.query, exported.counts.showing, exported.observations.length], ["SENSOR", 1, 1]);
  await setSearch(sessionId, "02:00:00:00:00:0");
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length > 1"), "address search");
  assert.equal(await evaluate(sessionId, "Array.from(document.querySelectorAll('#observations-body .address-column > span:first-child')).every(cell => cell.textContent.includes('02:00:00:00:00:0'))"), true);
  await setSearch(sessionId, "zzz-no-match");
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length === 0"), "no matches");
  assert.equal(await text(sessionId, "#empty-title"), "Eşleşen gözlem yok");
  assert.equal(await text(sessionId, '[data-node-id="pi-01"] .node-count'), "2");
  await click(sessionId, "#empty-clear");
  assert.equal(await evaluate(sessionId, "document.getElementById('search').value"), "");
  await setSearch(sessionId, "<img src=x");
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length === 1"), "literal HTML-like search");
  assert.equal(await evaluate(sessionId, "document.querySelector('main img') === null && window.__unsafe === undefined"), true);
  await click(sessionId, '[data-floor="3"] .floor-select');
  assert.equal(await text(sessionId, "#scope-title"), "Kat 3");
  assert.equal(await text(sessionId, "#empty-title"), "Eşleşen gözlem yok");
  await setSearch(sessionId, "shared");
  await click(sessionId, "#radio-wifi");
  assert.equal(await evaluate(sessionId, "document.getElementById('search').value"), "shared");
  assert(await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length > 0"));
  await click(sessionId, "#radio-bluetooth");
  await setSearch(sessionId, "");
  pass("Search is trimmed, case-insensitive plain text over in-scope names and addresses; filtered export and empty states match");

  await click(sessionId, '[data-floor="1"] .floor-select');
  const floorApi = await (await fetch(base + "/api/dashboard")).json();
  const floorOne = new Set(floorApi.nodes.filter((node) => node.floor === 1).map((node) => node.id));
  const floorGroups = floorApi.all_observations.bluetooth.filter((group) => group.observations.some((item) => floorOne.has(item.node_id)));
  await until(async () => await evaluate(sessionId, `document.querySelectorAll('#observations-body tr').length === ${floorGroups.length}`), "floor-scoped rows");
  const floorReporters = new Set(grouped.observations.filter((item) => floorOne.has(item.node_id)).map((item) => item.node_id));
  const floorShared = await evaluate(sessionId, `Array.from(document.querySelectorAll('#observations-body tr')).find(row => row.dataset.observationId === ${JSON.stringify(grouped.id)})?.lastElementChild.textContent`);
  assert(floorShared.includes(floorReporters.size > 1 ? `${floorReporters.size} Pi ·` : [...floorReporters].map((id) => floorApi.nodes.find((node) => node.id === id).label)[0]));
  await click(sessionId, '[data-node-id="pi-02"]');
  assert.equal(await text(sessionId, "#scope-floor"), "Kat 1");
  assert.equal(await evaluate(sessionId, "document.getElementById('clear-pi').getAttribute('aria-label')"), "Pi seçimini temizle");
  await click(sessionId, "#clear-pi");
  assert.equal(await text(sessionId, "#scope-title"), "Kat 1");
  await click(sessionId, "#scope-building");
  assert.equal(await text(sessionId, "#scope-title"), "Tüm gözlemler");
  pass("Floor scope removes other floors' reporters before counting; the breadcrumb returns to floor and building");

  const healthApi = await (await fetch(base + "/api/dashboard")).json();
  const expectedAttention = [];
  for (const node of healthApi.nodes) {
    if (node.state !== "online") expectedAttention.push(`${node.id}:`);
    else for (const radio of ["bluetooth", "wifi"]) if (["waiting", "stale", "error"].includes(node.radios[radio].state)) expectedAttention.push(`${node.id}:${radio}`);
  }
  await click(sessionId, "#pis-online");
  assert.equal(await evaluate(sessionId, "document.getElementById('pis-online').getAttribute('aria-expanded')"), "true");
  assert.equal(await text(sessionId, "#scope-title"), "Tüm gözlemler");
  assert.deepEqual((await evaluate(sessionId, "Array.from(document.querySelectorAll('.attention-item')).map(item => `${item.dataset.attentionNode}:${item.dataset.radio || ''}`)")).sort(), expectedAttention.sort());
  assert.equal(await text(sessionId, "#attention-count"), `${expectedAttention.length} uyarı`);
  assert((await text(sessionId, "#attention-disabled")).includes("Pi 08 Wi-Fi"));
  await click(sessionId, '.attention-item[data-attention-node="pi-04"]');
  assert.equal(await text(sessionId, "#scope-title"), "Pi 04");
  assert.equal(await evaluate(sessionId, "document.getElementById('radio-bluetooth').getAttribute('aria-pressed')"), "true");
  const banner = await text(sessionId, "#retained-banner");
  assert(banner.includes("Bluetooth taraması başarısız · önceki sonuçlar gösteriliyor") && banner.includes("Synthetic adapter failure"));
  assert.equal(await evaluate(sessionId, "!document.getElementById('retained-banner').hidden && document.getElementById('observations-body').classList.contains('is-retained')"), true);
  await click(sessionId, "#pis-online");
  assert.equal(await evaluate(sessionId, "document.getElementById('attention').hidden"), true);
  pass("Pis online opens the attention list without changing scope; entries open the affected Pi and radio with a retained-results banner");

  await setSearch(sessionId, "");
  await evaluate(sessionId, "window.__csv = null");
  const csvApi = await (await fetch(base + "/api/dashboard")).json();
  await click(sessionId, '[data-node-id="pi-01"]');
  await click(sessionId, "#scope-building");
  await click(sessionId, "#export-csv");
  await until(async () => await evaluate(sessionId, "typeof window.__csv === 'string'"), "CSV export");
  const csvLines = (await evaluate(sessionId, "window.__csv")).replace(/^\ufeff/, "").trimEnd().split("\r\n");
  assert.equal(csvLines[0].split(",")[13], "reporting_node_id");
  assert.equal(csvLines.length - 1, csvApi.all_observations.bluetooth.reduce((total, group) => total + group.observations.length, 0));
  assert(csvLines.some((line) => line.includes(",'=1+2,")));
  assert(!csvLines.some((line) => line.includes(",=1+2")));
  pass("CSV export lists one line per reporting Pi and neutralizes spreadsheet formulas in device names");

  // pi-07 never reports in the fixture loop; this task makes it appear and change while rows are focused.
  let lateSignal = -50;
  let lateRunning = true;
  const lateTask = (async () => {
    while (lateRunning) {
      await post(7, { kind: "heartbeat", enabled_radios: ["bluetooth", "wifi"] });
      await post(7, { kind: "scan", radio: "bluetooth", status: "success", observations: [{ path: "/fixture/pi-07/bt/late", properties: { Name: "Late arrival", Address: "02:00:00:00:07:07", RSSI: lateSignal-- } }] });
      await sleep(700);
    }
  })();
  try {
    await until(async () => await evaluate(sessionId, "!document.getElementById('new-rows').hidden"), "new-row indicator");
    const lateId = await evaluate(sessionId, "document.querySelector('#observations-body tr:last-child').dataset.observationId");
    assert(lateId.includes("02:00:00:00:07:07"));
    assert.equal(await text(sessionId, "#new-rows"), "1 yeni · Şimdi sırala");
    assert.equal(await evaluate(sessionId, "document.querySelector('#observations-body tr:last-child .new-tag').hidden"), false);
    await evaluate(sessionId, "document.querySelector('#observations-body tr:last-child .observation-select').focus()");
    const firstSignal = await text(sessionId, "#observations-body tr:last-child .signal-value");
    await until(async () => (await text(sessionId, "#observations-body tr:last-child .signal-value")) !== firstSignal, "signal updates in place");
    assert.equal(await evaluate(sessionId, "document.activeElement.closest('tr')?.dataset.observationId"), lateId);
    await click(sessionId, "#new-rows");
    assert.equal(await evaluate(sessionId, "document.getElementById('new-rows').hidden && document.querySelectorAll('.new-tag:not([hidden])').length === 0"), true);
  } finally {
    lateRunning = false;
    await lateTask;
  }
  pass("Polling appends new rows with a New marker, keeps focus on rows updated in place, and Sort now reorders");

  await click(sessionId, '[data-node-id="pi-04"]');
  await setSearch(sessionId, "beacon");
  await until(async () => (await evaluate(sessionId, "location.hash")).includes("q=beacon"), "hash records the query");
  assert((await evaluate(sessionId, "location.hash")).includes("pi=pi-04"));
  await cdp("Page.reload", {}, sessionId);
  await untilPage(sessionId, "document.getElementById('scope-title')?.textContent === 'Pi 04' && document.getElementById('search').value === 'beacon'", "hash restores the view after reload");
  await evaluate(sessionId, "history.back()");
  await untilPage(sessionId, "document.querySelectorAll('.node-marker').length === 9 && document.getElementById('scope-title').textContent === 'Tüm gözlemler'", "Back returns to the previous scope");
  await cdp("Page.navigate", { url: `${base}/#pi=pi-99&radio=bogus&sort=evil&floor=42&obs=%5B%22missing%22%5D` }, sessionId);
  await untilPage(sessionId, "location.hash === '' && document.getElementById('scope-title').textContent === 'Tüm gözlemler'", "invalid hash falls back to defaults");
  assert.deepEqual(await evaluate(sessionId, "[document.getElementById('sort').value, document.getElementById('radio-bluetooth').getAttribute('aria-pressed'), document.getElementById('detail-state').textContent]"), ["signal", "true", "Seçim yok"]);
  pass("URL hash restores scope and search after reload, Back returns to the previous scope, and invalid values fall back safely");

  await evaluate(sessionId, "document.activeElement.blur()");
  await key(sessionId, "/", "Slash", 191, "/");
  assert.deepEqual(await evaluate(sessionId, "[document.activeElement.id, document.getElementById('search').value]"), ["search", ""]);
  await key(sessionId, "w", "KeyW", 87, "w");
  assert.deepEqual(await evaluate(sessionId, "[document.getElementById('search').value, document.getElementById('radio-bluetooth').getAttribute('aria-pressed')]"), ["w", "true"]);
  await key(sessionId, "Escape", "Escape", 27);
  assert.equal(await evaluate(sessionId, "document.getElementById('search').value"), "");
  await until(async () => await evaluate(sessionId, "document.querySelectorAll('#observations-body tr').length > 1"), "rows for keyboard navigation");
  await evaluate(sessionId, "document.querySelector('#observations-body .observation-select').focus()");
  await key(sessionId, "ArrowDown", "ArrowDown", 40);
  const secondRow = await evaluate(sessionId, "document.querySelectorAll('#observations-body tr')[1].dataset.observationId");
  assert.deepEqual(await evaluate(sessionId, "[document.querySelector('#observations-body tr.selected')?.dataset.observationId, document.activeElement.closest('tr')?.dataset.observationId]"), [secondRow, secondRow]);
  await key(sessionId, "Escape", "Escape", 27);
  assert.equal(await evaluate(sessionId, "document.querySelector('#observations-body tr.selected')"), null);
  await evaluate(sessionId, "document.activeElement.blur()");
  await key(sessionId, "w", "KeyW", 87, "w");
  assert.equal(await evaluate(sessionId, "document.getElementById('radio-wifi').getAttribute('aria-pressed')"), "true");
  await key(sessionId, "b", "KeyB", 66, "b");
  assert.equal(await evaluate(sessionId, "document.getElementById('radio-bluetooth').getAttribute('aria-pressed')"), "true");
  pass("Keyboard: / focuses search, typing never triggers shortcuts, arrows move between rows, Esc clears, B/W switch radio");

  const themes = [];
  for (let index = 0; index < 3; index++) {
    await click(sessionId, "#theme-toggle");
    themes.push(await evaluate(sessionId, "[document.documentElement.dataset.theme || 'auto', getComputedStyle(document.body).backgroundColor]"));
  }
  assert.deepEqual(themes.map(([theme]) => theme), ["light", "dark", "auto"]);
  assert.equal(themes[1][1], "rgb(15, 22, 33)");
  assert.notEqual(themes[0][1], themes[1][1]);
  pass("Theme toggle cycles light, dark and automatic");

  await click(sessionId, '[data-node-id="pi-01"]');
  await screenshot(sessionId, "/tmp/nearby-building-desktop.png");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
  await sleep(100);
  assert.equal(await evaluate(sessionId, "document.documentElement.scrollWidth <= innerWidth"), true);
  assert.deepEqual(await smallText(sessionId), []);
  assert.deepEqual(await evaluate(sessionId, "[getComputedStyle(document.querySelector('.floor-plan')).display, document.getElementById('floors').offsetHeight < 400, getComputedStyle(document.getElementById('topbar')).position]"), ["none", true, "static"]);
  for (const node of config.nodes) await click(sessionId, `.node-marker[data-node-id="${node.id}"]`);
  await click(sessionId, "#map-mode");
  assert.equal(await evaluate(sessionId, "getComputedStyle(document.querySelector('.floor-plan')).display === 'block' && document.documentElement.scrollWidth <= innerWidth"), true);
  for (const node of config.nodes) await click(sessionId, `.node-marker[data-node-id="${node.id}"]`);
  await click(sessionId, "#map-mode");
  await click(sessionId, '[data-node-id="pi-01"]');
  await screenshot(sessionId, "/tmp/nearby-building-mobile.png");
  pass("390px mobile layout: compact floor overview, optional diagram, no overflow, readable text, every marker clickable");

  await cdp("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }, sessionId);
  await until(async () => await evaluate(sessionId, "!document.getElementById('connection-banner').hidden"), "disconnection banner");
  assert((await text(sessionId, "#connection-banner")).includes("güncel değil"));
  assert.equal(await text(sessionId, '[data-node-id="pi-01"] .node-count'), "—");
  assert.deepEqual(await evaluate(sessionId, "[document.getElementById('connection-state').textContent, document.querySelector('[data-node-id=\"pi-01\"]').dataset.state]"), ["Sunucuya ulaşılamıyor", "outdated"]);
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
  assert((await text(sessionId, "#status")).includes("Taramaya hazır"));
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
  assert.equal(await text(sessionId, ".device-select"), "Adsız cihaz");
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
  assert.equal(await text(secondSession, "#status"), "Tarama durduruldu.");
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
