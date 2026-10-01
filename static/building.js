"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const RADIOS = ["bluetooth", "wifi"];
  const SORTS = ["signal", "name", "address"];
  const stateLabels = { fresh: "Fresh", online: "Online", stale: "Stale", error: "Scan error", offline: "Offline", waiting: "Waiting", disabled: "Disabled", outdated: "Outdated" };
  const sortLabels = { signal: "strongest signal", name: "name", address: "address" };
  // Identity fields shown once only when every in-scope report supplies the same value.
  const identityFields = { bluetooth: ["Name", "Alias", "Address", "AddressType", "Icon", "Class", "Appearance"], wifi: ["SSID", "BSSID", "HwAddress", "Frequency", "Channel", "Security"] };
  const adapterFields = new Set(["Paired", "Connected", "Trusted", "Blocked", "Bonded", "ServicesResolved"]);
  const monoFields = new Set(["Address", "BSSID", "HwAddress", "SSIDHex", "Ssid", "UUIDs", "Adapter", "Modalias"]);
  const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

  let snapshot = null;
  let connected = false;
  let attempted = false;
  let receivedAt = null;
  const view = { radio: "bluetooth", floor: null, nodeId: null, query: "", sort: "signal" };
  let observationId = null;
  let previewId = null;
  let pendingObservation = null;
  let selectionNote = "";
  let attentionOpen = false;
  // Row order is kept between polls; it is recomputed only on an explicit change or Sort now.
  let order = [];
  let orderKey = "";
  const appended = new Set();
  const rowElements = new Map();
  let lastRows = [];
  let layoutKey = "";
  let breadcrumbKey = "";
  let attentionKey = "";
  let detailsKey = "";
  const openDetails = new Set();
  const markers = new Map();
  let inFlight = false;
  let pollTimer = null;
  let hashTimer = null;

  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }
  function svgElement(tag, attributes) {
    const result = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes)) result.setAttribute(name, String(value));
    return result;
  }

  function radioName(name = view.radio) { return name === "wifi" ? "Wi-Fi" : "Bluetooth"; }
  function label(status) { return stateLabels[status] || "Unknown"; }
  function isNumber(value) { return typeof value === "number" && Number.isFinite(value); }
  function plural(count, one, many) { return count === 1 ? one : many; }
  function ageText(age) {
    if (!isNumber(age)) return "not yet received";
    if (age < 1) return "just now";
    if (age < 60) return `${Math.floor(age)}s ago`;
    if (age < 3600) return `${Math.floor(age / 60)}m ago`;
    return `${Math.floor(age / 3600)}h ago`;
  }
  function valueText(value) {
    if (value === null || value === undefined) return "Unknown";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (value === "") return '""';
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }
  function textProperty(value) { return typeof value === "string" && value.length ? value : null; }
  function recordName(properties) {
    if (view.radio === "wifi") return properties.SSID === "" ? "Hidden network" : textProperty(properties.SSID) || "Unknown network";
    return textProperty(properties.Name) || textProperty(properties.Alias) || "Unknown device";
  }
  function hasName(properties) { return view.radio === "wifi" ? typeof properties.SSID === "string" : !!(textProperty(properties.Name) || textProperty(properties.Alias)); }
  function address(properties) {
    return textProperty(view.radio === "wifi" ? properties.BSSID || properties.HwAddress : properties.Address) || "Unknown";
  }
  function signalValue(properties) { return view.radio === "wifi" ? properties.Strength : properties.RSSI; }
  function formatSignal(value) { return isNumber(value) ? `${value}${view.radio === "wifi" ? "%" : " dBm"}` : "Unknown signal"; }
  function signalText(properties) { return formatSignal(signalValue(properties)); }
  // Meter length: Bluetooth -100 to -30 dBm and Wi-Fi 0 to 100 % both map onto 0 to 100 %.
  function signalPercent(value) {
    if (!isNumber(value)) return null;
    const percent = view.radio === "wifi" ? value : ((value + 100) / 70) * 100;
    return Math.max(0, Math.min(100, Math.round(percent)));
  }
  function bandName(frequency) {
    if (!isNumber(frequency)) return null;
    if (frequency >= 2400 && frequency < 2500) return "2.4 GHz";
    if (frequency >= 4900 && frequency < 5925) return "5 GHz";
    if (frequency >= 5925 && frequency <= 7125) return "6 GHz";
    return null;
  }
  function channelText(properties) {
    return [isNumber(properties.Channel) ? `Ch ${properties.Channel}` : null, bandName(properties.Frequency)].filter(Boolean).join(" · ") || null;
  }
  function deviceType(properties) {
    const icon = textProperty(properties.Icon);
    return icon ? icon.charAt(0).toUpperCase() + icon.slice(1).replace(/[-_]+/g, " ") : null;
  }
  function nodeById(id) { return snapshot?.nodes.find((node) => node.id === id) || null; }
  function selectedNode() { return view.nodeId ? nodeById(view.nodeId) : null; }
  function nodeLabel(id) { return nodeById(id)?.label || id; }
  function normalizedQuery() { return view.query.trim().toLowerCase(); }

  const iconShapes = {
    fresh: [["circle", { cx: 12, cy: 12, r: 6, fill: "currentColor", stroke: "none" }]],
    stale: [["circle", { cx: 12, cy: 12, r: 8.5 }], ["path", { d: "M12 7.5V12l3 2" }]],
    error: [["path", { d: "M12 3.5 21.5 20h-19z" }], ["path", { d: "M12 10v4.5m0 2.7v.3" }]],
    offline: [["circle", { cx: 12, cy: 12, r: 8.5 }], ["path", { d: "m6 6 12 12" }]],
    waiting: [6, 12, 18].map((cx) => ["circle", { cx, cy: 12, r: 1.8, fill: "currentColor", stroke: "none" }]),
    disabled: [["circle", { cx: 12, cy: 12, r: 8.5 }], ["path", { d: "M8 12h8" }]],
    outdated: [["path", { d: "M9 6v12m6-12v12" }]],
  };
  iconShapes.online = iconShapes.fresh;
  function stateIcon(state) {
    const svg = svgElement("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "state-icon" });
    for (const [tag, attributes] of iconShapes[state] || iconShapes.offline) svg.append(svgElement(tag, attributes));
    return svg;
  }
  function setState(target, state, text = label(state)) {
    target.className = `${target.className.split(" ").filter((name) => !name.startsWith("state")).join(" ")} state state-${state}`.trim();
    target.replaceChildren(stateIcon(state), document.createTextNode(text));
    return target;
  }
  function stateBadge(state, text) { return setState(element("span"), state, text); }

  function signalMeter() {
    const meter = element("span", "meter");
    meter.setAttribute("aria-hidden", "true");
    meter.append(element("i"));
    return meter;
  }
  function setMeter(meter, value) {
    const percent = signalPercent(value);
    meter.classList.toggle("unknown", percent === null);
    meter.style.setProperty("--level", `${percent ?? 0}%`);
  }

  function piIcon() {
    const svg = svgElement("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.5, "aria-hidden": "true" });
    svg.append(svgElement("path", { d: "M5 5h14v14H5zM9 9h6v6H9zM8 2v3m4-3v3m4-3v3M8 19v3m4-3v3m4-3v3M2 8h3m-3 4h3m-3 4h3m14-8h3m-3 4h3m-3 4h3" }));
    return svg;
  }

  // Rows for the current radio and scope, before search. Floor scope drops other floors' reporters first.
  function scopedRows() {
    if (!snapshot) return [];
    const node = selectedNode();
    if (node) {
      return node.radios[view.radio].observations.map((record) => ({ id: JSON.stringify([node.id, record.path]), observations: [{ node_id: node.id, ...record }] }));
    }
    const groups = snapshot.all_observations[view.radio];
    if (view.floor === null) return groups;
    const floorNodes = new Set(snapshot.nodes.filter((item) => item.floor === view.floor).map((item) => item.id));
    return groups
      .map((group) => ({ id: group.id, observations: group.observations.filter((item) => floorNodes.has(item.node_id)) }))
      .filter((group) => group.observations.length);
  }
  function searchText(properties) {
    const values = view.radio === "wifi"
      ? [recordName(properties), properties.SSID, properties.BSSID, properties.HwAddress]
      : [recordName(properties), properties.Name, properties.Alias, properties.Address];
    return values.filter((value) => typeof value === "string").join("\n").toLowerCase();
  }
  function matches(row, needle) { return !needle || row.observations.some((item) => searchText(item.properties).includes(needle)); }
  function representative(row) { return row.observations.find((item) => hasName(item.properties)) || row.observations[0]; }
  function strongest(row) {
    let value = null;
    let nodeId = null;
    for (const item of row.observations) {
      const signal = signalValue(item.properties);
      if (isNumber(signal) && (value === null || signal > value)) { value = signal; nodeId = item.node_id; }
    }
    return { value, nodeId };
  }
  function compareRows(left, right) {
    let difference = 0;
    if (view.sort === "signal") {
      const a = strongest(left).value;
      const b = strongest(right).value;
      difference = a === null ? (b === null ? 0 : 1) : b === null ? -1 : b - a;
    } else {
      const key = (row) => {
        const properties = representative(row).properties;
        return view.sort === "name" ? [hasName(properties) ? 0 : 1, recordName(properties)] : [address(properties) === "Unknown" ? 1 : 0, address(properties)];
      };
      const [knownA, textA] = key(left);
      const [knownB, textB] = key(right);
      difference = knownA - knownB || collator.compare(textA, textB);
    }
    return difference || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
  }
  function orderRows(rows) {
    const key = JSON.stringify([view.radio, view.floor, view.nodeId, normalizedQuery(), view.sort]);
    const byId = new Map(rows.map((row) => [row.id, row]));
    if (key !== orderKey || !order.length) {
      orderKey = key;
      order = rows.slice().sort(compareRows).map((row) => row.id);
      appended.clear();
    } else {
      const kept = order.filter((id) => byId.has(id));
      const known = new Set(kept);
      const added = rows.filter((row) => !known.has(row.id)).sort(compareRows);
      for (const id of appended) if (!byId.has(id)) appended.delete(id);
      for (const row of added) appended.add(row.id);
      order = kept.concat(added.map((row) => row.id));
    }
    return order.map((id) => byId.get(id));
  }
  function freshness(node) {
    if (!connected) return "outdated";
    return node && node.radios[view.radio].state !== "fresh" ? "retained" : "fresh";
  }

  function validateView() {
    const node = selectedNode();
    let changed = false;
    if (view.nodeId && !node) { view.nodeId = null; changed = true; }
    if (node && view.floor !== node.floor) view.floor = node.floor;
    if (view.floor !== null && !snapshot.nodes.some((item) => item.floor === view.floor)) { view.floor = null; changed = true; }
    if (changed) writeHash(false);
  }

  function renderTopbar() {
    const online = snapshot.nodes.filter((item) => item.state === "online").length;
    const freshNodes = snapshot.nodes.filter((item) => item.radios[view.radio].state === "fresh");
    $("online-count").textContent = connected ? `${online} / ${snapshot.nodes.length}` : "—";
    $("observation-count").textContent = connected && freshNodes.length ? snapshot.all_observations[view.radio].length : "—";
    $("observation-unit").textContent = view.radio === "wifi" ? "current access points" : "current observations";
    for (const name of RADIOS) {
      $(`radio-${name}`).classList.toggle("active", view.radio === name);
      $(`radio-${name}`).setAttribute("aria-pressed", String(view.radio === name));
    }
    $("export-button").disabled = false;
    $("export-csv").disabled = false;
    $("export-button").title = `Download the displayed ${radioName()} rows as JSON, with scope, search, sort and freshness context`;
    $("export-csv").title = `Download the displayed ${radioName()} rows as CSV: one line per reporting Pi`;
    $("radio-note").textContent = view.radio === "wifi"
      ? "Wi-Fi shows nearby access points, not connected phones or laptops. Signal quality is a percentage. Shared SSIDs can belong to different access points."
      : "Bluetooth shows discovery observations, including Classic and BLE. Rotating addresses may appear separately. Signal strength is in dBm and does not measure distance.";
  }

  function renderBuilding() {
    const key = JSON.stringify(snapshot.nodes.map(({ id, label: name, floor, x, y }) => [id, name, floor, x, y]));
    if (key !== layoutKey) {
      layoutKey = key;
      markers.clear();
      const floors = document.createDocumentFragment();
      const numbers = [...new Set(snapshot.nodes.map((node) => node.floor))].sort((a, b) => b - a);
      for (const number of numbers) {
        const section = $("floor-template").content.firstElementChild.cloneNode(true);
        section.dataset.floor = String(number);
        const select = section.querySelector(".floor-select");
        select.textContent = `Floor ${number}`;
        select.addEventListener("click", () => setScope({ floor: number, nodeId: null }));
        for (const node of snapshot.nodes.filter((item) => item.floor === number)) {
          const button = element("button", "node-marker");
          button.type = "button";
          button.dataset.nodeId = node.id;
          button.style.left = `${node.x}%`;
          button.style.top = `${node.y}%`;
          const symbol = element("span", "pi-symbol");
          const count = element("span", "node-count");
          symbol.append(piIcon(), count);
          const status = element("span", "node-state");
          const signal = element("span", "node-signal");
          signal.hidden = true;
          button.append(symbol, element("span", "node-label", node.label), status, signal);
          button.addEventListener("click", () => setScope({ floor: node.floor, nodeId: node.id }));
          section.querySelector(".marker-layer").append(button);
          markers.set(node.id, { button, count, status, signal, state: "", baseLabel: "" });
        }
        floors.append(section);
      }
      $("floors").replaceChildren(floors);
    }
    for (const node of snapshot.nodes) {
      const marker = markers.get(node.id);
      const result = node.radios[view.radio];
      const status = connected ? result.state : "outdated";
      const count = connected && result.state === "fresh" ? result.count : "—";
      marker.button.classList.toggle("selected", node.id === view.nodeId);
      marker.button.setAttribute("aria-pressed", String(node.id === view.nodeId));
      marker.baseLabel = `${node.label}, ${node.location}, ${radioName()}: ${connected ? label(status) : "view outdated"}, ${count === "—" ? "count unavailable" : `${count} ${plural(count, "observation", "observations")}`}`;
      marker.button.title = `${node.label} · ${node.location} · ${radioName()} ${connected ? label(status) : "view outdated"}`;
      marker.count.textContent = count;
      if (marker.state !== status) {
        marker.state = status;
        setState(marker.status, status);
        marker.status.classList.add("node-state");
      }
      marker.button.dataset.state = status;
    }
    for (const floor of document.querySelectorAll(".floor")) {
      const number = Number(floor.dataset.floor);
      const nodes = snapshot.nodes.filter((node) => node.floor === number);
      const online = nodes.filter((node) => node.state === "online").length;
      floor.querySelector(".floor-status").textContent = connected ? `${online} / ${nodes.length} online` : "View outdated";
      floor.classList.toggle("selected-floor", view.floor === number);
      const select = floor.querySelector(".floor-select");
      if (view.floor === number) select.setAttribute("aria-current", "true");
      else select.removeAttribute("aria-current");
    }
  }

  // The selected (or hovered/focused) observation outlines the Pis that reported it.
  function renderMapLink() {
    const id = previewId ?? observationId;
    const row = id ? lastRows.find((item) => item.id === id) : null;
    const signals = new Map();
    if (row) {
      for (const item of row.observations) {
        const value = signalValue(item.properties);
        const previous = signals.get(item.node_id);
        if (!signals.has(item.node_id) || (isNumber(value) && (!isNumber(previous) || value > previous))) signals.set(item.node_id, value);
      }
    }
    for (const [nodeId, marker] of markers) {
      const reports = signals.has(nodeId);
      const node = nodeById(nodeId);
      const old = reports && (!connected || node?.radios[view.radio].state !== "fresh");
      marker.button.classList.toggle("reports", reports);
      marker.button.classList.toggle("quiet", !!row && !reports);
      marker.signal.hidden = !reports;
      marker.signal.textContent = reports ? `${formatSignal(signals.get(nodeId))}${old ? " · old" : ""}` : "";
      marker.signal.classList.toggle("old", old);
      marker.button.setAttribute("aria-label", marker.baseLabel + (reports ? `. Reported the ${id === observationId ? "selected" : "highlighted"} observation at ${formatSignal(signals.get(nodeId))}${old ? ", previous result" : ""}` : ""));
    }
    const caption = $("map-caption");
    caption.hidden = !row;
    if (row) {
      const name = recordName(representative(row).properties);
      const prefix = id === observationId ? "" : "Preview: ";
      caption.textContent = `${prefix}${signals.size} outlined ${plural(signals.size, "Pi", "Pis")} reported “${name}”. Each marker shows its own signal reading; this does not locate the device.`;
    }
  }

  function attentionItems() {
    const items = [];
    const disabled = [];
    for (const node of snapshot.nodes) {
      if (node.state === "offline" || node.state === "waiting") {
        items.push({ node, radio: null, state: node.state, text: node.state === "offline" ? "Pi offline" : "No report received yet" });
        continue;
      }
      for (const radio of RADIOS) {
        const state = node.radios[radio].state;
        if (state === "disabled") disabled.push(`${node.label} ${radioName(radio)}`);
        else if (["waiting", "stale", "error"].includes(state)) items.push({ node, radio, state, text: `${radioName(radio)} · ${label(state)}` });
      }
    }
    return { items, disabled };
  }
  function renderAttention() {
    const { items, disabled } = attentionItems();
    const count = $("attention-count");
    count.textContent = !connected ? "Health unavailable · view outdated" : items.length ? `${items.length} ${plural(items.length, "needs", "need")} attention` : "Nothing needs attention";
    count.classList.toggle("has-issues", connected && items.length > 0);
    $("pis-online").setAttribute("aria-expanded", String(attentionOpen));
    $("attention").hidden = !attentionOpen;
    if (!attentionOpen) {
      attentionKey = "";
      return;
    }
    $("attention-note").textContent = connected ? "Select an entry to open that Pi" : "View outdated";
    const key = JSON.stringify(items.map((item) => [item.node.id, item.node.label, item.radio, item.state]));
    if (key !== attentionKey) {
      attentionKey = key;
      const list = document.createDocumentFragment();
      for (const item of items) {
        const button = element("button", "attention-item");
        button.type = "button";
        button.dataset.attentionNode = item.node.id;
        if (item.radio) button.dataset.radio = item.radio;
        button.append(element("strong", "", item.node.label), stateBadge(item.state, item.text));
        button.addEventListener("click", () => {
          if (item.radio) view.radio = item.radio;
          setScope({ floor: item.node.floor, nodeId: item.node.id }, { force: true });
        });
        const entry = element("li");
        entry.append(button);
        list.append(entry);
      }
      $("attention-list").replaceChildren(list);
    }
    $("attention-empty").hidden = items.length > 0;
    $("attention-disabled").hidden = !disabled.length;
    $("attention-disabled").textContent = disabled.length ? `Disabled radios (not failures): ${disabled.join(", ")}` : "";
  }

  function renderScope(node) {
    const key = JSON.stringify([view.floor, node?.id, node?.label]);
    if (key !== breadcrumbKey) {
      breadcrumbKey = key;
      const crumb = (text, id, next) => {
        const button = element("button", "crumb-button", text);
        button.type = "button";
        button.id = id;
        button.addEventListener("click", () => { setScope(next); $("scope-title").focus({ preventScroll: true }); });
        return button;
      };
      const current = (text) => {
        const span = element("span", "crumb-current", text);
        span.setAttribute("aria-current", "location");
        return span;
      };
      const separator = () => {
        const span = element("span", "crumb-separator", "›");
        span.setAttribute("aria-hidden", "true");
        return span;
      };
      const parts = [];
      if (view.floor === null && !node) parts.push(current("Building"));
      else {
        parts.push(crumb("Building", "scope-building", { floor: null, nodeId: null }), separator());
        if (!node) parts.push(current(`Floor ${view.floor}`));
        else {
          parts.push(crumb(`Floor ${node.floor}`, "scope-floor", { floor: node.floor, nodeId: null }), separator(), current(node.label));
          const clear = element("button", "crumb-clear", "×");
          clear.type = "button";
          clear.id = "clear-pi";
          clear.title = "Clear Pi selection";
          clear.setAttribute("aria-label", "Clear Pi selection");
          clear.addEventListener("click", () => { setScope({ floor: node.floor, nodeId: null }); $("scope-title").focus({ preventScroll: true }); });
          parts.push(clear);
        }
      }
      $("breadcrumb").replaceChildren(...parts);
    }
    const floorNodes = view.floor === null ? [] : snapshot.nodes.filter((item) => item.floor === view.floor);
    $("scope-title").textContent = node ? node.label : view.floor !== null ? `Floor ${view.floor}` : "All observations";
    $("scope-caption").textContent = node
      ? `Floor ${node.floor} · ${node.id} · ${label(node.state)}`
      : view.floor !== null ? `${floorNodes.length} ${plural(floorNodes.length, "Pi", "Pis")} on this floor · fresh results only` : "Across all reporting Pis · fresh results only";
    $("node-details").hidden = !node;
    if (!node) return;
    $("node-location").textContent = node.location;
    $("node-ip").textContent = node.ip || "Not yet received";
    $("node-heartbeat").textContent = ageText(node.heartbeat_age);
    $("node-contact").textContent = ageText(node.age);
    const health = document.createDocumentFragment();
    for (const name of RADIOS) {
      const results = node.radios[name];
      const state = connected ? results.state : "outdated";
      const text = connected
        ? `${radioName(name)} · ${label(results.state)} · ${results.age === null ? "no results yet" : `scan report ${ageText(results.age)}`}`
        : `${radioName(name)} · last known ${label(results.state)}`;
      const badge = stateBadge(state, text);
      badge.classList.add("health", state);
      badge.dataset.radio = name;
      health.append(badge);
      if (results.error) health.append(element("p", "radio-error", `${radioName(name)}: ${results.error}`));
    }
    $("radio-health").replaceChildren(health);
  }

  function renderSummary(rows, scoped, node) {
    const noun = view.radio === "wifi" ? ["access point", "access points"] : ["observation", "observations"];
    const nouns = (count) => plural(count, noun[0], noun[1]);
    const result = node?.radios[view.radio];
    const scopeNodes = node ? [node] : view.floor !== null ? snapshot.nodes.filter((item) => item.floor === view.floor) : snapshot.nodes;
    const hasFresh = scopeNodes.some((item) => item.radios[view.radio].state === "fresh");
    let summary;
    if (!connected) summary = `Showing ${rows.length} of ${scoped.length} saved ${nouns(scoped.length)} · outdated view`;
    else if (node && result.state !== "fresh") summary = scoped.length ? `Showing ${rows.length} of ${scoped.length} previous ${nouns(scoped.length)} · not current` : `No current count · ${label(result.state)}`;
    else if (!node && !hasFresh) summary = "No current count · awaiting fresh reports";
    else summary = `Showing ${rows.length} of ${scoped.length} ${nouns(scoped.length)}`;
    $("results-summary").textContent = summary;
    const context = !connected ? "Reconnect to verify freshness"
      : node ? (result.state === "fresh" ? `Fresh scan · report ${ageText(result.age)}` : `Scan report ${ageText(result.age)}`)
        : "Fresh results only";
    $("results-context").textContent = `${context} · by ${sortLabels[view.sort]}`;
    $("name-heading").textContent = view.radio === "wifi" ? "Network (SSID)" : "Device";
    $("address-heading").textContent = view.radio === "wifi" ? "BSSID" : "Address";
    const pill = $("new-rows");
    pill.hidden = !appended.size;
    pill.textContent = `${appended.size} new · Sort now`;
    pill.title = "Rows that appeared since the last sort were added at the end. Sort now to reorder.";

    const banner = $("retained-banner");
    const retained = connected && node && result.state !== "fresh" && scoped.length > 0;
    banner.hidden = !retained;
    if (retained) {
      const headline = {
        error: `${radioName()} scan failed · showing previous results`,
        stale: `${radioName()} results are stale · showing previous results`,
        offline: "Pi offline · showing previous results",
        disabled: `${radioName()} is disabled · showing previous results`,
      }[result.state] || "Showing previous results";
      const text = element("span");
      const error = (result.error || "").trim();
      text.append(element("strong", "", headline), document.createTextNode(`Scan report ${ageText(result.age)}.${error ? ` ${error}${/[.!?]$/.test(error) ? "" : "."}` : ""} These rows are not current.`));
      banner.replaceChildren(stateIcon(result.state === "error" ? "error" : "stale"), text);
    }
  }

  function createRow(id) {
    const tr = element("tr");
    tr.dataset.observationId = id;
    const button = element("button", "observation-select");
    button.type = "button";
    const newTag = element("span", "new-tag", "New");
    newTag.hidden = true;
    const nameAddress = element("span", "name-address");
    const link = element("button", "details-link", "Show details ↓");
    link.type = "button";
    link.addEventListener("click", () => {
      $("details").scrollIntoView({ block: "start" });
      $("details-title").focus({ preventScroll: true });
    });
    const nameSub = element("span", "cell-sub");
    const nameCell = element("td");
    nameCell.append(button, newTag, nameSub, nameAddress, link);
    const addressText = element("span");
    const addressSub = element("span", "cell-sub");
    const addressCell = element("td", "address address-column");
    addressCell.append(addressText, addressSub);
    const meter = signalMeter();
    const signalLabel = element("span", "signal-value");
    const signalLine = element("span", "signal-line");
    signalLine.append(meter, signalLabel);
    const reporterText = element("span", "cell-sub");
    const signalCell = element("td");
    signalCell.append(signalLine, reporterText);
    tr.append(nameCell, addressCell, signalCell);
    tr.addEventListener("click", (event) => { if (!event.target.closest(".details-link")) selectObservation(id); });
    tr.addEventListener("mouseenter", () => setPreview(id));
    tr.addEventListener("mouseleave", () => setPreview(null));
    button.addEventListener("focus", () => setPreview(id));
    button.addEventListener("blur", () => setPreview(null));
    tr.addEventListener("animationend", () => tr.classList.remove("appended"));
    return { tr, button, newTag, nameSub, nameAddress, addressText, addressSub, meter, signalLabel, reporterText, key: "" };
  }
  // Count distinct Pis; the strongest known value comes only from in-scope reporters.
  function reporterSummary(row) {
    const nodes = new Set(row.observations.map((item) => item.node_id));
    const best = strongest(row);
    if (nodes.size === 1) return { value: best.value, text: nodeLabel([...nodes][0]) };
    return { value: best.value, text: best.value === null ? `${nodes.size} Pis · signal unknown` : `${nodes.size} Pis · strongest at ${nodeLabel(best.nodeId)}` };
  }
  function updateRow(entry, row) {
    const properties = representative(row).properties;
    const name = recordName(properties);
    const shown = address(properties);
    const nameSub = view.radio === "wifi" ? textProperty(properties.Security) : deviceType(properties);
    const addressSub = view.radio === "wifi" ? channelText(properties) : textProperty(properties.AddressType);
    const summary = reporterSummary(row);
    const selected = row.id === observationId;
    const isNew = appended.has(row.id);
    const key = JSON.stringify([view.radio, name, shown, nameSub, addressSub, summary, selected, isNew]);
    if (key === entry.key) return;
    entry.key = key;
    entry.button.textContent = name;
    entry.button.title = name;
    entry.button.setAttribute("aria-pressed", String(selected));
    entry.tr.classList.toggle("selected", selected);
    entry.newTag.hidden = !isNew;
    entry.nameSub.textContent = nameSub || "";
    entry.nameSub.hidden = !nameSub;
    entry.nameAddress.textContent = view.radio === "wifi" && addressSub ? `${shown} · ${addressSub}` : shown;
    entry.addressText.textContent = shown;
    entry.addressSub.textContent = addressSub || "";
    entry.addressSub.hidden = !addressSub;
    entry.signalLabel.textContent = formatSignal(summary.value);
    entry.reporterText.textContent = summary.text;
    setMeter(entry.meter, summary.value);
  }
  // Update rows in place by ID so polling never replaces a focused control.
  function renderRows(rows, node) {
    const body = $("observations-body");
    body.classList.toggle("is-retained", freshness(node) !== "fresh");
    const present = new Set(rows.map((row) => row.id));
    for (const [id, entry] of rowElements) {
      if (!present.has(id)) {
        entry.tr.remove();
        rowElements.delete(id);
      }
    }
    const active = document.activeElement;
    let expected = body.firstElementChild;
    for (const row of rows) {
      let entry = rowElements.get(row.id);
      if (!entry) {
        entry = createRow(row.id);
        rowElements.set(row.id, entry);
        if (appended.has(row.id)) entry.tr.classList.add("appended");
      }
      updateRow(entry, row);
      if (entry.tr === expected) expected = expected.nextElementSibling;
      else body.insertBefore(entry.tr, expected);
    }
    if (active && active !== document.activeElement && body.contains(active)) active.focus({ preventScroll: true });
  }

  function renderEmpty(rows, scoped, node) {
    $("empty-state").hidden = rows.length !== 0;
    $("empty-clear").hidden = true;
    if (rows.length) return;
    const result = node?.radios[view.radio];
    let title = view.floor !== null && !node ? `No current observations on Floor ${view.floor}` : "No current observations";
    let description = view.floor !== null && !node ? "Results appear when a Pi on this floor completes a fresh scan." : "Results appear automatically when a Pi completes a fresh scan.";
    if (scoped.length) {
      title = view.radio === "wifi" ? "No matching access points" : "No matching observations";
      description = `Nothing in this view matches “${view.query.trim()}”. The scanners' results and Pi counts are unchanged.`;
      $("empty-clear").hidden = false;
    } else if (node && result.state === "fresh") {
      title = view.radio === "wifi" ? "No access points discovered" : "No devices discovered";
      description = "This Pi's latest scan completed successfully with zero observations.";
    } else if (node && result.state === "disabled") {
      title = `${radioName()} is disabled`;
      description = "This radio is disabled in the Pi's agent configuration.";
    } else if (node && result.state === "error") {
      title = `${radioName()} scan needs attention`;
      description = result.error || "The Pi reported a scan error. The other radio can continue reporting.";
    } else if (node && result.state === "offline") {
      title = "This Pi is offline";
      description = "No recent reports have reached the server. Other Pis continue independently.";
    } else if (node && result.state === "waiting") {
      title = `Waiting for ${radioName()}`;
      description = "This Pi has not sent a successful scan for the selected radio yet.";
    }
    if (!connected && !scoped.length) {
      title = "Waiting for the server";
      description = "This view is outdated. It will recover automatically when the server responds.";
    }
    $("empty-title").textContent = title;
    $("empty-description").textContent = description;
  }

  function displayName(name) {
    if (name === "Strength" && view.radio === "wifi") return "Strength (%)";
    return { RSSI: "RSSI (dBm)", Frequency: "Frequency (MHz)", MaxBitrate: "MaxBitrate (advertised Kb/s)", TxPower: "TxPower (dBm)" }[name] || name;
  }
  function isMono(name, value) {
    return monoFields.has(name) || (typeof value === "string" && /^(?:[0-9a-f]{2}[:-]){2,}[0-9a-f]{2}$|^(?:0x)?[0-9a-f]{6,}$/i.test(value));
  }
  function valueElement(tag, name, value) {
    const result = element(tag);
    if (value !== null && typeof value === "object") result.append(element("pre", "", valueText(value)));
    else {
      result.textContent = valueText(value);
      if (isMono(name, value)) result.classList.add("mono");
    }
    return result;
  }
  function detailSection(title) {
    const section = element("section", "detail-section");
    section.append(element("h4", "", title));
    return section;
  }
  function buildDetails(row, node) {
    const content = document.createDocumentFragment();
    const properties = representative(row).properties;
    const reporters = new Set(row.observations.map((item) => item.node_id));
    const state = freshness(node);
    const summary = element("div", "detail-summary");
    summary.append(element("h3", "", recordName(properties)), element("p", "detail-address", address(properties)));
    const chips = element("div", "chips");
    chips.append(element("span", "chip", view.radio === "wifi" ? "Wi-Fi access point" : "Bluetooth observation"), element("span", "chip", `${reporters.size} reporting ${plural(reporters.size, "Pi", "Pis")}`));
    if (state !== "fresh") chips.append(element("span", "chip old", state === "outdated" ? "Outdated view" : "Previous result"));
    summary.append(chips);
    content.append(summary);

    const same = [];
    const differ = [];
    for (const field of identityFields[view.radio]) {
      const values = row.observations.map((item) => (Object.hasOwn(item.properties, field) ? item.properties[field] : undefined));
      if (values.every((value) => value === undefined)) continue;
      const identical = values.every((value) => value !== undefined && (value === null || typeof value !== "object") && value === values[0]);
      (identical ? same : differ).push({ field, values });
    }
    if (same.length) {
      const section = detailSection(row.observations.length > 1 ? `Same in all ${row.observations.length} reports` : "Identity");
      const list = element("dl", "kv");
      for (const { field, values } of same) list.append(element("dt", "", displayName(field)), valueElement("dd", field, values[0]));
      section.append(list);
      content.append(section);
    }
    if (differ.length) {
      const section = detailSection("Differs between reports");
      section.append(element("p", "section-note", "Each Pi's value is kept as reported; nothing is merged."));
      const list = element("dl", "differences");
      for (const { field, values } of differ) {
        const group = element("div");
        const values_ = element("ul");
        row.observations.forEach((item, index) => {
          const entry = element("li", "", `${nodeLabel(item.node_id)}: `);
          entry.append(values[index] === undefined ? element("span", "absent", "not reported") : valueElement("span", field, values[index]));
          values_.append(entry);
        });
        const description = element("dd");
        description.append(values_);
        group.append(element("dt", "", displayName(field)), description);
        list.append(group);
      }
      section.append(list);
      content.append(section);
    }

    const measures = detailSection(row.observations.length > 1 ? "Per-Pi measurements" : "Measurement");
    const table = element("table", "measure-table");
    const head = element("tr");
    for (const text of ["Pi", "Signal", "Report"]) {
      const cell = element("th", "", text);
      cell.scope = "col";
      head.append(cell);
    }
    const headGroup = element("thead");
    headGroup.append(head);
    const bodyGroup = element("tbody");
    for (const item of row.observations) {
      const reporter = nodeById(item.node_id);
      const result = reporter?.radios[view.radio];
      const tr = element("tr");
      const piCell = element("td");
      piCell.append(element("strong", "", nodeLabel(item.node_id)));
      if (reporter) piCell.append(element("span", "location", reporter.location));
      const signalCell = element("td", "signal-cell");
      const meter = signalMeter();
      setMeter(meter, signalValue(item.properties));
      signalCell.append(meter, document.createTextNode(signalText(item.properties)));
      if (!connected || result?.state !== "fresh") signalCell.append(element("span", "old-tag", "old"));
      const reportCell = element("td");
      const age = element("span", "location");
      age.dataset.ageNode = item.node_id;
      reportCell.append(stateBadge(connected ? result?.state || "offline" : "outdated"), age);
      tr.append(piCell, signalCell, reportCell);
      bodyGroup.append(tr);
    }
    table.append(headGroup, bodyGroup);
    measures.append(table);
    content.append(measures);

    const raw = detailSection("All reported properties");
    raw.append(element("p", "section-note", "Original names and values from each report, including the scanner object path."));
    for (const item of row.observations) {
      const key = `${item.node_id}\n${item.path}`;
      const details = element("details", "reporter-detail");
      details.open = openDetails.has(key);
      details.addEventListener("toggle", () => { if (details.open) openDetails.add(key); else openDetails.delete(key); });
      const heading = element("summary", "", `${nodeLabel(item.node_id)} · ${signalText(item.properties)} · ${Object.keys(item.properties).length} properties`);
      heading.dataset.focusKey = key;
      details.append(heading, element("p", "object-path", item.path));
      if (view.radio === "bluetooth" && Object.keys(item.properties).some((name) => adapterFields.has(name))) {
        details.append(element("p", "adapter-note", "Paired, Connected and similar values describe this Pi's adapter, not building-wide presence."));
      }
      const list = element("dl", "properties-list");
      for (const [name, value] of Object.entries(item.properties).sort(([left], [right]) => left.localeCompare(right))) {
        const property = element("div", "property");
        property.append(element("dt", "", displayName(name)), valueElement("dd", name, value));
        list.append(property);
      }
      details.append(list);
      raw.append(details);
    }
    content.append(raw);
    return content;
  }
  function updateDetailAges() {
    for (const cell of $("properties").querySelectorAll("[data-age-node]")) {
      const result = nodeById(cell.dataset.ageNode)?.radios[view.radio];
      cell.textContent = result ? `Scan report ${ageText(result.age)}` : "Scan report unknown";
    }
  }
  function renderDetails(rows, node) {
    const row = rows.find((item) => item.id === observationId);
    const state = freshness(node);
    $("details-empty").hidden = !!row;
    $("properties").hidden = !row;
    $("detail-state").textContent = !row ? (selectionNote ? "Selection cleared" : "No selection") : state === "outdated" ? "Outdated snapshot" : state === "retained" ? "Previous result · not current" : "Fresh observation";
    if (!row) {
      $("details-empty").textContent = selectionNote || "Select an observation to see its identity, each reporting Pi's measurement and every reported property.";
      detailsKey = "";
      $("properties").replaceChildren();
      return;
    }
    const key = JSON.stringify([view.radio, connected, row, row.observations.map((item) => nodeById(item.node_id)?.radios[view.radio].state)]);
    if (key !== detailsKey) {
      detailsKey = key;
      const container = $("properties");
      const focusKey = document.activeElement?.closest?.("#properties [data-focus-key]")?.dataset.focusKey;
      const scrollTop = $("details").scrollTop;
      container.replaceChildren(buildDetails(row, node));
      $("details").scrollTop = scrollTop;
      if (focusKey) Array.from(container.querySelectorAll("[data-focus-key]")).find((item) => item.dataset.focusKey === focusKey)?.focus({ preventScroll: true });
    }
    updateDetailAges();
  }

  function render() {
    if (!snapshot) return;
    validateView();
    const node = selectedNode();
    const scoped = scopedRows();
    const needle = normalizedQuery();
    const rows = orderRows(scoped.filter((row) => matches(row, needle)));
    lastRows = rows;
    if (pendingObservation !== null) {
      if (rows.some((row) => row.id === pendingObservation)) observationId = pendingObservation;
      pendingObservation = null;
      if (!observationId) writeHash(false);
    }
    if (observationId && !rows.some((row) => row.id === observationId)) {
      observationId = null;
      openDetails.clear();
      selectionNote = "The selected observation is no longer in this view: it left the current results or no longer matches the search.";
      writeHash(false);
    }
    if (previewId && !rows.some((row) => row.id === previewId)) previewId = null;
    renderTopbar();
    renderBuilding();
    renderAttention();
    renderScope(node);
    renderSummary(rows, scoped, node);
    renderRows(rows, node);
    renderEmpty(rows, scoped, node);
    renderDetails(rows, node);
    renderMapLink();
  }

  function setPreview(id) {
    if (previewId === id) return;
    previewId = id;
    renderMapLink();
  }
  function selectObservation(id) {
    if (observationId !== id) openDetails.clear();
    observationId = id;
    selectionNote = "";
    writeHash(false);
    render();
  }
  function clearSelection() {
    observationId = null;
    selectionNote = "";
    openDetails.clear();
    writeHash(false);
    render();
  }
  function setScope(next, { force = false } = {}) {
    const changed = force || view.floor !== next.floor || view.nodeId !== next.nodeId;
    view.floor = next.floor;
    view.nodeId = next.nodeId;
    observationId = null;
    previewId = null;
    selectionNote = "";
    openDetails.clear();
    if (changed) writeHash(true);
    render();
  }
  function setRadio(radio) {
    if (view.radio === radio) return;
    view.radio = radio;
    observationId = null;
    previewId = null;
    selectionNote = "";
    openDetails.clear();
    writeHash(true);
    render();
  }
  function setQuery(value) {
    view.query = value.slice(0, 200);
    if ($("search").value !== view.query) $("search").value = view.query;
    $("clear-search").hidden = !view.query;
    scheduleHash();
    render();
  }
  function sortNow() {
    orderKey = "";
    render();
  }
  function moveSelection(delta) {
    const ids = lastRows.map((row) => row.id);
    if (!ids.length) return;
    const focused = document.activeElement?.closest?.("tr")?.dataset.observationId;
    let index = ids.indexOf(focused ?? observationId);
    index = index < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, index + delta));
    selectObservation(ids[index]);
    const button = rowElements.get(ids[index])?.button;
    button?.focus({ preventScroll: true });
    button?.scrollIntoView({ block: "nearest" });
  }

  // View controls live in the URL hash: scope/radio changes add history entries, the rest replace.
  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const radio = params.get("radio");
    const sort = params.get("sort");
    const floor = params.get("floor");
    return {
      radio: RADIOS.includes(radio) ? radio : "bluetooth",
      sort: SORTS.includes(sort) ? sort : "signal",
      floor: /^\d{1,3}$/.test(floor || "") ? Number(floor) : null,
      nodeId: params.get("pi") || null,
      query: (params.get("q") || "").slice(0, 200),
      observation: params.get("obs"),
    };
  }
  function applyHash() {
    const state = readHash();
    Object.assign(view, { radio: state.radio, sort: state.sort, floor: state.floor, nodeId: state.nodeId, query: state.query });
    $("search").value = view.query;
    $("clear-search").hidden = !view.query;
    $("sort").value = view.sort;
    observationId = null;
    previewId = null;
    selectionNote = "";
    pendingObservation = state.observation;
    openDetails.clear();
    orderKey = "";
  }
  function writeHash(push) {
    clearTimeout(hashTimer);
    const params = new URLSearchParams();
    if (view.radio !== "bluetooth") params.set("radio", view.radio);
    if (view.floor !== null) params.set("floor", String(view.floor));
    if (view.nodeId) params.set("pi", view.nodeId);
    if (view.query) params.set("q", view.query);
    if (view.sort !== "signal") params.set("sort", view.sort);
    if (observationId) params.set("obs", observationId);
    const text = params.toString();
    if (text === location.hash.slice(1)) return;
    history[push ? "pushState" : "replaceState"](null, "", text ? `#${text}` : location.pathname + location.search);
  }
  function scheduleHash() {
    clearTimeout(hashTimer);
    hashTimer = setTimeout(() => writeHash(false), 300);
  }

  function scopeSlug() {
    const node = selectedNode();
    return `${node ? node.id : view.floor !== null ? `floor-${view.floor}` : "all"}${view.query.trim() ? "-filtered" : ""}`;
  }
  function exportScope() {
    const node = selectedNode();
    const scope = node
      ? { type: "node", node_id: node.id, label: node.label, floor: node.floor, location: node.location }
      : view.floor !== null
        ? { type: "floor", floor: view.floor, note: "Grouped results fresh at the time of the last received snapshot; reporters on other floors are removed" }
        : { type: "all_observations", note: "Grouped results fresh at the time of the last received snapshot" };
    const nodes = node ? [node] : view.floor !== null ? snapshot.nodes.filter((item) => item.floor === view.floor) : snapshot.nodes;
    return { node, scope, nodes };
  }
  function download(text, type, filename) {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const link = element("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function exportJson() {
    if (!snapshot) return;
    const { node, scope, nodes } = exportScope();
    const payload = {
      exported_at: new Date().toISOString(),
      snapshot_received_at: receivedAt.toISOString(),
      outdated: !connected,
      radio: view.radio,
      scope,
      filters: { query: view.query.trim(), sort: view.sort, order: "as displayed" },
      counts: { showing: lastRows.length, in_scope: scopedRows().length },
      nodes: nodes.map((item) => ({
        id: item.id, label: item.label, floor: item.floor, location: item.location,
        ip: item.ip, state: item.state, age: item.age, heartbeat_age: item.heartbeat_age,
        radio_state: item.radios[view.radio].state, radio_age: item.radios[view.radio].age, radio_error: item.radios[view.radio].error,
      })),
      observations: node ? lastRows.map((row) => {
        const { path, properties } = row.observations[0];
        return { path, properties };
      }) : lastRows,
    };
    download(JSON.stringify(payload, null, 2), "application/json", `nearby-${view.radio}-${scopeSlug()}.json`);
  }
  // Device names are untrusted: neutralize spreadsheet formulas and quote CSV syntax.
  function csvCell(value) {
    if (value === null || value === undefined) return "";
    let text = String(value);
    if (typeof value === "string" && /^[=+\-@\t\r]/.test(text) && !/^[+-]?\d+(?:\.\d+)?$/.test(text)) text = `'${text}`;
    return /[",\r\n]|^\s|\s$/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  }
  function exportCsv() {
    if (!snapshot) return;
    const { scope } = exportScope();
    const columns = ["exported_at", "snapshot_received_at", "outdated", "radio", "scope", "scope_floor", "scope_node_id", "query", "sort",
      "row", "observation_id", "display_name", "address", "reporting_node_id", "reporting_node_label", "reporting_floor", "reporting_location",
      "signal", "signal_unit", "radio_state", "scan_report_age_s", "object_path", "properties_json"];
    const common = [new Date().toISOString(), receivedAt.toISOString(), !connected, view.radio, scope.type, scope.floor ?? null, scope.node_id ?? null, view.query.trim(), view.sort];
    const lines = [columns.join(",")];
    lastRows.forEach((row, index) => {
      const properties = representative(row).properties;
      for (const item of row.observations) {
        const reporter = nodeById(item.node_id);
        const result = reporter?.radios[view.radio];
        const signal = signalValue(item.properties);
        const values = [...common, index + 1, row.id, recordName(properties), address(properties), item.node_id, reporter?.label, reporter?.floor, reporter?.location,
          isNumber(signal) ? signal : null, view.radio === "wifi" ? "percent" : "dBm", result?.state, result?.age, item.path, JSON.stringify(item.properties)];
        lines.push(values.map(csvCell).join(","));
      }
    });
    download(`﻿${lines.join("\r\n")}\r\n`, "text/csv;charset=utf-8", `nearby-${view.radio}-${scopeSlug()}.csv`);
  }

  function renderConnection() {
    $("connection").className = `connection ${connected ? "connected" : attempted ? "disconnected" : ""}`;
    $("connection-state").textContent = connected ? "Server connected" : attempted ? "Server unavailable" : "Connecting";
    let age = "";
    if (receivedAt) {
      const seconds = (Date.now() - receivedAt.getTime()) / 1000;
      age = connected ? `Dashboard received ${ageText(seconds)}` : `Last received ${receivedAt.toLocaleTimeString()} · outdated`;
    }
    $("connection-age").textContent = age;
    $("connection-banner").hidden = connected || !attempted;
    $("connection-banner").textContent = snapshot
      ? "Connection lost. The last received view is outdated; counts are unavailable until the server responds. Retrying automatically."
      : "Cannot reach the dashboard server. Waiting for a response and retrying automatically.";
  }
  function updateStickyTop() {
    const bar = $("topbar");
    const sticky = getComputedStyle(bar).position === "sticky";
    document.documentElement.style.setProperty("--sticky-top", sticky ? `${bar.offsetHeight}px` : "0px");
  }

  async function poll() {
    if (inFlight) return;
    inFlight = true;
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch("/api/dashboard", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(`Dashboard HTTP ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data.nodes) || !Array.isArray(data.all_observations?.bluetooth) || !Array.isArray(data.all_observations?.wifi)) throw new Error("Invalid dashboard response");
      snapshot = data;
      connected = true;
      receivedAt = new Date();
    } catch (_) {
      connected = false;
    } finally {
      attempted = true;
      clearTimeout(deadline);
      inFlight = false;
      renderConnection();
      render();
      pollTimer = setTimeout(poll, document.hidden ? 10000 : 2000);
    }
  }

  const legend = document.createDocumentFragment();
  for (const state of ["fresh", "stale", "error", "offline", "waiting", "disabled"]) {
    const item = element("li");
    item.append(stateBadge(state));
    legend.append(item);
  }
  $("legend").replaceChildren(legend);
  $("details-title").tabIndex = -1;
  $("scope-title").tabIndex = -1;

  // Theme: automatic follows the OS through CSS; an explicit choice is a per-browser convenience.
  const themeOrder = ["auto", "light", "dark"];
  const themeNames = { auto: "automatic (follows your system)", light: "light", dark: "dark" };
  const themeIcons = {
    auto: [["circle", { cx: 12, cy: 12, r: 8 }], ["path", { d: "M12 4a8 8 0 0 1 0 16z", fill: "currentColor" }]],
    light: [["circle", { cx: 12, cy: 12, r: 4 }], ["path", { d: "M12 2.5v2m0 15v2M4.6 4.6 6 6m12 12 1.4 1.4M2.5 12h2m15 0h2M4.6 19.4 6 18M18 6l1.4-1.4" }]],
    dark: [["path", { d: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" }]],
  };
  function storedTheme() {
    try {
      const theme = localStorage.getItem("nearby-theme");
      return themeOrder.includes(theme) ? theme : "auto";
    } catch (_) {
      return "auto";
    }
  }
  function applyTheme(theme) {
    if (theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    const next = themeOrder[(themeOrder.indexOf(theme) + 1) % themeOrder.length];
    const button = $("theme-toggle");
    button.dataset.theme = theme;
    button.title = `Theme: ${themeNames[theme]}`;
    button.setAttribute("aria-label", `Theme: ${themeNames[theme]}. Switch to ${next === "auto" ? "automatic" : next} theme`);
    const icon = svgElement("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    for (const [tag, attributes] of themeIcons[theme]) icon.append(svgElement(tag, attributes));
    button.replaceChildren(icon);
  }
  $("theme-toggle").addEventListener("click", () => {
    const next = themeOrder[(themeOrder.indexOf($("theme-toggle").dataset.theme) + 1) % themeOrder.length];
    try {
      if (next === "auto") localStorage.removeItem("nearby-theme");
      else localStorage.setItem("nearby-theme", next);
    } catch (_) {
      // Storage can be unavailable; the choice still applies to this page.
    }
    applyTheme(next);
  });
  applyTheme(storedTheme());

  for (const name of RADIOS) $(`radio-${name}`).addEventListener("click", () => setRadio(name));
  $("pis-online").addEventListener("click", () => {
    attentionOpen = !attentionOpen;
    render();
    if (attentionOpen) $("attention").scrollIntoView({ block: "nearest" });
  });
  $("map-mode").addEventListener("click", () => {
    const diagram = !$("map-mode").classList.contains("active");
    $("map-mode").classList.toggle("active", diagram);
    $("map-mode").setAttribute("aria-pressed", String(diagram));
    $("map-mode").textContent = diagram ? "Show overview" : "Show diagram";
    document.querySelector(".building").classList.toggle("show-diagram", diagram);
  });
  $("search").addEventListener("input", () => setQuery($("search").value));
  $("search").maxLength = 200;
  $("clear-search").addEventListener("click", () => { setQuery(""); $("search").focus(); });
  $("empty-clear").addEventListener("click", () => { setQuery(""); $("search").focus(); });
  $("sort").addEventListener("change", () => {
    view.sort = SORTS.includes($("sort").value) ? $("sort").value : "signal";
    writeHash(false);
    render();
  });
  $("sort-now").title = "Polling keeps the current order and adds new rows at the end. Sort now reorders every row.";
  $("sort-now").addEventListener("click", sortNow);
  $("new-rows").addEventListener("click", sortNow);
  $("export-button").addEventListener("click", exportJson);
  $("export-csv").addEventListener("click", exportCsv);
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target instanceof Element ? event.target : null;
    const editing = target?.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])");
    if (event.key === "Escape") {
      if (target === $("search") && $("search").value) {
        event.preventDefault();
        setQuery("");
      } else if (!editing && observationId) {
        event.preventDefault();
        clearSelection();
      } else if (!editing && attentionOpen) {
        attentionOpen = false;
        render();
      }
      return;
    }
    if (editing) return;
    if (event.key === "/") {
      event.preventDefault();
      $("search").focus();
      $("search").select();
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && target?.closest("#observations-body")) {
      event.preventDefault();
      moveSelection(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "b" || event.key === "B") setRadio("bluetooth");
    else if (event.key === "w" || event.key === "W") setRadio("wifi");
  });
  window.addEventListener("popstate", () => { applyHash(); render(); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      clearTimeout(pollTimer);
      poll();
    }
  });
  new ResizeObserver(updateStickyTop).observe($("topbar"));
  window.addEventListener("resize", updateStickyTop);
  setInterval(renderConnection, 1000);
  applyHash();
  updateStickyTop();
  poll();
})();
