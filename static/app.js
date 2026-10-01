"use strict";

const $ = (id) => document.getElementById(id);
let snapshot = null;
let selectedPath = null;
let lastDevices = null;
let lastDetail = null;
let actionPending = false;
let connected = false;
let requestVersion = 0;
const labels = {
  Name: "Name", Address: "Bluetooth address", AddressType: "Address type",
  RSSI: "Signal strength (dBm)", TxPower: "Transmit power (dBm)",
  UUIDs: "Service UUIDs", Class: "Device class", Appearance: "Appearance",
  ManufacturerData: "Manufacturer data", ServiceData: "Service data",
  Paired: "Paired", Connected: "Connected", ServicesResolved: "Services resolved",
};

function text(value) {
  if (value === null || value === undefined || value === "") return "Unknown";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function deviceName(record) {
  const props = record.properties;
  if (props.Name) return props.Name;
  const alias = props.Alias;
  const address = props.Address || "";
  if (alias && alias.replaceAll("-", ":").toLowerCase() !== address.toLowerCase()) return alias;
  return "Unnamed device";
}

function updateButtons() {
  const running = Boolean(snapshot?.scanning);
  $("scan-button").disabled = !connected || actionPending || running;
  $("duration").disabled = actionPending || running;
  $("stop-button").disabled = !connected || actionPending || !running || snapshot.stopping;
  $("export-button").disabled = !snapshot?.devices.length;
  $("scan-button").textContent = running ? "Scanning…" : "Start scan";
}

function renderDetails() {
  const record = snapshot?.devices.find((item) => item.path === selectedPath);
  const signature = JSON.stringify(record);
  if (signature === lastDetail) return;
  lastDetail = signature;
  $("details-empty").hidden = Boolean(record);
  $("details-content").hidden = !record;
  $("detail-label").textContent = record ? "Available information" : "No selection";
  if (!record) return;
  $("device-name").textContent = deviceName(record);
  $("device-address").textContent = text(record.properties.Address);
  const fragment = document.createDocumentFragment();
  for (const [name, value] of Object.entries(record.properties).sort(([a], [b]) => a.localeCompare(b))) {
    const row = document.createElement("div");
    row.className = "property";
    const label = document.createElement("dt");
    label.textContent = labels[name] || name.replace(/([a-z])([A-Z])/g, "$1 $2");
    const content = document.createElement("dd");
    content.textContent = text(value);
    if (typeof value === "object" && value !== null) content.className = "raw";
    row.append(label, content);
    fragment.append(row);
  }
  $("properties").replaceChildren(fragment);
}

function selectDevice(path) {
  selectedPath = path;
  for (const row of $("devices").children) {
    const selected = row.dataset.path === path;
    row.classList.toggle("selected", selected);
    row.querySelector("button").setAttribute("aria-pressed", String(selected));
  }
  renderDetails();
}

function renderDevices() {
  const signature = JSON.stringify(snapshot.devices);
  if (signature === lastDevices) return;
  lastDevices = signature;
  if (!snapshot.devices.some((record) => record.path === selectedPath)) {
    selectedPath = snapshot.devices[0]?.path || null;
  }
  const focusedPath = document.activeElement?.closest("tr[data-path]")?.dataset.path;
  const fragment = document.createDocumentFragment();
  for (const record of snapshot.devices) {
    const props = record.properties;
    const row = document.createElement("tr");
    row.dataset.path = record.path;
    row.classList.toggle("selected", record.path === selectedPath);
    const nameCell = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "device-select";
    button.textContent = deviceName(record);
    button.title = deviceName(record);
    button.setAttribute("aria-pressed", String(record.path === selectedPath));
    nameCell.append(button);
    row.append(nameCell);
    const values = [text(props.Address), props.RSSI == null ? "Unknown" : `${props.RSSI} dBm`, text(props.Paired), text(props.Connected)];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 0) cell.className = "address";
      if (index === 1) cell.className = "signal";
      row.append(cell);
    });
    fragment.append(row);
  }
  $("devices").replaceChildren(fragment);
  if (focusedPath) {
    [...$("devices").children].find((row) => row.dataset.path === focusedPath)?.querySelector("button").focus({ preventScroll: true });
  }
  $("device-count").textContent = String(snapshot.devices.length);
  $("empty-state").hidden = snapshot.devices.length > 0;
  renderDetails();
}

function render(data) {
  snapshot = data;
  connected = true;
  $("connection").textContent = data.stopping ? "Stopping" : data.scanning ? "Scanning" : "Connected";
  $("connection").className = `connection ${data.scanning ? "scanning" : "ready"}`;
  $("status").textContent = data.message;
  $("error").hidden = !data.error;
  $("error").textContent = data.error || "";
  $("progress").hidden = !data.scanning;
  $("progress").max = data.timeout;
  $("progress").value = Math.min(data.elapsed, data.timeout);
  $("scan-time").textContent = data.scanning ? `${Math.floor(data.elapsed)}s / ${data.timeout}s` : "";
  $("empty-title").textContent = data.scanning ? "Looking for devices…" : data.elapsed > 0 ? "No devices discovered" : "Your next discovery starts here";
  $("empty-description").textContent = data.scanning ? "Results appear here as devices are discovered." : data.elapsed > 0 ? "Make sure a device is advertising or discoverable, then try again." : "Start a scan to find nearby Bluetooth devices.";
  renderDevices();
  updateButtons();
}

async function request(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 6000);
  try {
    const options = { signal: controller.signal, cache: "no-store" };
    if (body !== undefined) {
      options.method = "POST";
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The request failed.");
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function poll() {
  if (actionPending) {
    setTimeout(poll, 1000);
    return;
  }
  const version = ++requestVersion;
  try {
    const data = await request("/api/status");
    if (version === requestVersion) render(data);
  } catch {
    if (version === requestVersion) {
      connected = false;
      $("connection").textContent = "Offline";
      $("connection").className = "connection offline";
      $("status").textContent = "Cannot reach the scanner. Check that the server is running. Retrying…";
      updateButtons();
    }
  } finally {
    setTimeout(poll, document.hidden ? 5000 : 1000);
  }
}

async function action(path, body) {
  actionPending = true;
  const version = ++requestVersion;
  updateButtons();
  try {
    const data = await request(path, body);
    if (version === requestVersion) render(data);
  } catch (error) {
    $("error").textContent = error.name === "AbortError" ? "The scanner did not respond. Check the connection and try again." : error.message;
    $("error").hidden = false;
  } finally {
    actionPending = false;
    updateButtons();
  }
}

$("scan-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if ($("scan-button").disabled || !$("scan-form").reportValidity()) return;
  action("/api/scan", { timeout: Number($("duration").value) });
});
$("stop-button").addEventListener("click", () => action("/api/stop", {}));
$("devices").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-path]");
  if (row) selectDevice(row.dataset.path);
});
$("export-button").addEventListener("click", () => {
  if (!snapshot?.devices.length) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot.devices, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "bluetooth-devices.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
poll();
