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
  Name: "Ad", Alias: "Takma ad", Address: "Bluetooth adresi", AddressType: "Adres türü",
  RSSI: "Sinyal gücü (dBm)", TxPower: "Yayın gücü (dBm)",
  UUIDs: "Servis UUID'leri", Class: "Cihaz sınıfı", Appearance: "Görünüm",
  ManufacturerData: "Üretici verisi", ServiceData: "Servis verisi",
  Paired: "Eşleşmiş", Connected: "Bağlı", ServicesResolved: "Servisler çözümlendi",
  Trusted: "Güvenilir", Blocked: "Engellenmiş", Icon: "Simge",
};
// The server and scanner report in English; known messages are shown in Turkish, anything else as sent.
const serverMessages = {
  "Ready to scan.": "Taramaya hazır.",
  "Starting discovery...": "Keşif başlatılıyor...",
  "Stopping discovery...": "Keşif durduruluyor...",
  "Scan stopped.": "Tarama durduruldu.",
  "Scan complete.": "Tarama tamamlandı.",
  "Could not start the scanner worker. Please retry.": "Tarama işlemi başlatılamadı. Lütfen yeniden deneyin.",
  "A scan is already running or the server is closing.": "Zaten bir tarama sürüyor ya da sunucu kapanıyor.",
  "Scan duration must be a number between 1 and 300 seconds.": "Tarama süresi 1 ile 300 saniye arasında bir sayı olmalıdır.",
  "Scan duration must be between 1 and 300 seconds.": "Tarama süresi 1 ile 300 saniye arasında olmalıdır.",
  "No Bluetooth adapter found. Connect or enable a Bluetooth adapter.": "Bluetooth adaptörü bulunamadı. Bir Bluetooth adaptörü bağlayın veya etkinleştirin.",
  "Bluetooth is off or blocked. Enable the adapter in Bluetooth settings or with bluetoothctl and retry.": "Bluetooth kapalı veya engellenmiş. Adaptörü Bluetooth ayarlarından ya da bluetoothctl ile açıp yeniden deneyin.",
  "Lost connection to the system D-Bus during the scan. Please retry.": "Tarama sırasında sistem D-Bus bağlantısı koptu. Lütfen yeniden deneyin.",
  "Access to the system D-Bus was denied. Check your user permissions.": "Sistem D-Bus erişimi reddedildi. Kullanıcı izinlerinizi kontrol edin.",
  "Connecting to the system D-Bus timed out.": "Sistem D-Bus bağlantısı zaman aşımına uğradı.",
};
const serverPatterns = [
  [/^Scanning Classic and BLE on (\S+) for ([\d.]+) seconds\.\.\.$/, (adapter, seconds) => `${adapter} üzerinde Classic ve BLE ${seconds} saniye taranıyor...`],
  [/^Configured Bluetooth adapter '(.+)' was not found\.$/, (adapter) => `Yapılandırılan Bluetooth adaptörü '${adapter}' bulunamadı.`],
];
function translate(message) {
  if (!message) return message;
  if (serverMessages[message]) return serverMessages[message];
  for (const [pattern, format] of serverPatterns) {
    const match = message.match(pattern);
    if (match) return format(...match.slice(1));
  }
  return message;
}

function text(value) {
  if (value === null || value === undefined || value === "") return "Bilinmiyor";
  if (typeof value === "boolean") return value ? "Evet" : "Hayır";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function deviceName(record) {
  const props = record.properties;
  if (props.Name) return props.Name;
  const alias = props.Alias;
  const address = props.Address || "";
  if (alias && alias.replaceAll("-", ":").toLowerCase() !== address.toLowerCase()) return alias;
  return "Adsız cihaz";
}

function updateButtons() {
  const running = Boolean(snapshot?.scanning);
  $("scan-button").disabled = !connected || actionPending || running;
  $("duration").disabled = actionPending || running;
  $("stop-button").disabled = !connected || actionPending || !running || snapshot.stopping;
  $("export-button").disabled = !snapshot?.devices.length;
  $("scan-button").textContent = running ? "Taranıyor…" : "Taramayı başlat";
}

function renderDetails() {
  const record = snapshot?.devices.find((item) => item.path === selectedPath);
  const signature = JSON.stringify(record);
  if (signature === lastDetail) return;
  lastDetail = signature;
  $("details-empty").hidden = Boolean(record);
  $("details-content").hidden = !record;
  $("detail-label").textContent = record ? "Mevcut bilgiler" : "Seçim yok";
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
    const values = [text(props.Address), props.RSSI == null ? "Bilinmiyor" : `${props.RSSI} dBm`, text(props.Paired), text(props.Connected)];
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
  $("connection").textContent = data.stopping ? "Durduruluyor" : data.scanning ? "Taranıyor" : "Bağlı";
  $("connection").className = `connection ${data.scanning ? "scanning" : "ready"}`;
  $("status").textContent = translate(data.message);
  $("error").hidden = !data.error;
  $("error").textContent = translate(data.error) || "";
  $("progress").hidden = !data.scanning;
  $("progress").max = data.timeout;
  $("progress").value = Math.min(data.elapsed, data.timeout);
  $("scan-time").textContent = data.scanning ? `${Math.floor(data.elapsed)} sn / ${data.timeout} sn` : "";
  $("empty-title").textContent = data.scanning ? "Cihazlar aranıyor…" : data.elapsed > 0 ? "Cihaz bulunamadı" : "Bir sonraki keşfin burada başlıyor";
  $("empty-description").textContent = data.scanning ? "Cihazlar bulundukça sonuçlar burada görünür." : data.elapsed > 0 ? "Bir cihazın yayın yaptığından veya keşfedilebilir olduğundan emin olup yeniden deneyin." : "Yakındaki Bluetooth cihazlarını bulmak için bir tarama başlatın.";
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
    if (!response.ok) throw new Error(translate(data.error) || "İstek başarısız oldu.");
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
      $("connection").textContent = "Çevrimdışı";
      $("connection").className = "connection offline";
      $("status").textContent = "Tarayıcıya ulaşılamıyor. Sunucunun çalıştığını kontrol edin. Yeniden deneniyor…";
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
    $("error").textContent = error.name === "AbortError" ? "Tarayıcı yanıt vermedi. Bağlantıyı kontrol edip yeniden deneyin." : error.message;
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
