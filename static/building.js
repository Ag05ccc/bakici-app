"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const stateLabels = { fresh: "Fresh", online: "Online", stale: "Stale", error: "Scan error", offline: "Offline", waiting: "Waiting", disabled: "Disabled" };
  let snapshot = null;
  let connected = false;
  let receivedAt = null;
  let radio = "bluetooth";
  let nodeId = null;
  let observationId = null;
  let layoutKey = "";
  let rowsKey = "";
  let detailsKey = "";
  let inFlight = false;
  let pollTimer = null;
  const markers = new Map();

  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function radioName(name = radio) { return name === "wifi" ? "Wi-Fi" : "Bluetooth"; }
  function label(status) { return stateLabels[status] || "Unknown"; }
  function isNumber(value) { return typeof value === "number" && Number.isFinite(value); }
  function ageText(age) {
    if (!isNumber(age)) return "Not yet received";
    if (age < 1) return "Just now";
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
    if (radio === "wifi") return properties.SSID === "" ? "Hidden network" : textProperty(properties.SSID) || "Unknown network";
    return textProperty(properties.Name) || textProperty(properties.Alias) || "Unknown device";
  }
  function address(properties) {
    return textProperty(radio === "wifi" ? properties.BSSID || properties.HwAddress : properties.Address) || "Unknown";
  }
  function signalValue(properties) { return radio === "wifi" ? properties.Strength : properties.RSSI; }
  function signalText(properties) {
    const value = signalValue(properties);
    return isNumber(value) ? `${value}${radio === "wifi" ? "%" : " dBm"}` : "Unknown signal";
  }
  function selectedNode() { return snapshot?.nodes.find((node) => node.id === nodeId) || null; }
  function nodeLabel(id) { return snapshot?.nodes.find((node) => node.id === id)?.label || id; }

  function piIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.5");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", "M5 5h14v14H5zM9 9h6v6H9zM8 2v3m4-3v3m4-3v3M8 19v3m4-3v3m4-3v3M2 8h3m-3 4h3m-3 4h3m14-8h3m-3 4h3m-3 4h3");
    svg.append(path);
    return svg;
  }

  function renderBuilding() {
    const key = JSON.stringify(snapshot.nodes.map(({ id, label: name, floor, x, y }) => [id, name, floor, x, y]));
    if (key !== layoutKey) {
      layoutKey = key;
      markers.clear();
      const floors = document.createDocumentFragment();
      for (const number of [3, 2, 1]) {
        const section = $("floor-template").content.firstElementChild.cloneNode(true);
        section.dataset.floor = String(number);
        section.querySelector("h3").textContent = `Floor ${number}`;
        for (const node of snapshot.nodes.filter((item) => item.floor === number)) {
          const button = element("button", "node-marker");
          button.type = "button";
          button.dataset.nodeId = node.id;
          button.style.left = `${node.x}%`;
          button.style.top = `${node.y}%`;
          const symbol = element("span", "pi-symbol");
          const count = element("span", "node-count");
          symbol.append(piIcon(), count);
          const name = element("span", "node-label", node.label);
          const status = element("span", "node-state");
          button.append(symbol, name, status);
          button.addEventListener("click", () => {
            nodeId = node.id;
            observationId = null;
            render();
          });
          section.querySelector(".marker-layer").append(button);
          markers.set(node.id, { button, count, status });
        }
        floors.append(section);
      }
      $("floors").replaceChildren(floors);
    }
    for (const node of snapshot.nodes) {
      const marker = markers.get(node.id);
      const result = node.radios[radio];
      const status = connected ? result.state : "offline";
      const count = connected && result.state === "fresh" ? result.count : "—";
      marker.button.className = `node-marker ${status}${node.id === nodeId ? " selected" : ""}`;
      marker.button.setAttribute("aria-pressed", String(node.id === nodeId));
      marker.button.setAttribute("aria-label", `${node.label}, ${node.location}, ${radioName()}: ${connected ? label(status) : "view outdated"}, ${count === "—" ? "count unavailable" : `${count} observations`}`);
      marker.button.title = `${node.label} · ${node.location} · ${radioName()} ${connected ? label(status) : "view outdated"}`;
      marker.count.textContent = count;
      marker.status.textContent = connected ? label(status) : "Outdated";
      marker.button.dataset.state = status;
    }
    for (const floor of document.querySelectorAll(".floor")) {
      const nodes = snapshot.nodes.filter((node) => node.floor === Number(floor.dataset.floor));
      const online = nodes.filter((node) => node.state === "online").length;
      floor.querySelector(".floor-status").textContent = connected ? `${online} / ${nodes.length} online` : "View outdated";
    }
  }

  function renderNode(node) {
    $("node-details").hidden = !node;
    $("all-observations").classList.toggle("active", !node);
    $("all-observations").setAttribute("aria-pressed", String(!node));
    $("scope-title").textContent = node ? node.label : "All observations";
    $("scope-caption").textContent = node ? `Floor ${node.floor} · ${node.id} · ${label(node.state)}` : "Across all reporting Pis";
    if (!node) return;
    $("node-location").textContent = node.location;
    $("node-ip").textContent = node.ip || "Not yet received";
    $("node-heartbeat").textContent = ageText(node.heartbeat_age);
    $("node-contact").textContent = ageText(node.age);
    const health = document.createDocumentFragment();
    for (const name of ["bluetooth", "wifi"]) {
      const results = node.radios[name];
      const text = `${radioName(name)} · ${label(results.state)} · ${results.age === null ? "No results yet" : ageText(results.age)}`;
      const badge = element("span", `health ${results.state}`, text);
      badge.dataset.radio = name;
      health.append(badge);
      if (results.error) health.append(element("p", "radio-error", `${radioName(name)}: ${results.error}`));
    }
    $("radio-health").replaceChildren(health);
  }

  function visibleRows() {
    if (!snapshot) return [];
    const node = selectedNode();
    const rows = node ? node.radios[radio].observations.map((record) => ({
      id: JSON.stringify([node.id, record.path]), observations: [{ node_id: node.id, ...record }],
    })) : snapshot.all_observations[radio];
    return rows.slice().sort((left, right) => {
      const strongest = (group) => group.observations.reduce((best, item) => {
        const value = signalValue(item.properties);
        return isNumber(value) ? Math.max(best, value) : best;
      }, -Infinity);
      const difference = strongest(right) - strongest(left);
      return (Number.isNaN(difference) ? 0 : difference) || left.id.localeCompare(right.id);
    });
  }

  function renderRows(rows, node) {
    const result = node?.radios[radio];
    const hasFresh = snapshot.nodes.some((item) => item.radios[radio].state === "fresh");
    const current = connected && (node ? result.state === "fresh" : hasFresh);
    const noun = radio === "wifi" ? "access point" : "observation";
    if (!connected) $("results-summary").textContent = `${rows.length} saved ${noun}${rows.length === 1 ? "" : "s"} · outdated view`;
    else if (node && result.state !== "fresh") $("results-summary").textContent = `— current · ${label(result.state)}`;
    else if (!node && !hasFresh) $("results-summary").textContent = "— current · awaiting fresh reports";
    else $("results-summary").textContent = `${rows.length} ${noun}${rows.length === 1 ? "" : "s"}`;
    $("results-context").textContent = !connected ? "Reconnect to verify freshness" : node ? (current ? `Fresh scan · ${ageText(result.age)}` : `Retained results · ${result.age === null ? "none received" : ageText(result.age)}`) : "Fresh results only · strongest signal first";
    $("name-heading").textContent = radio === "wifi" ? "Network (SSID)" : "Device";
    $("address-heading").textContent = radio === "wifi" ? "BSSID" : "Address";
    if (observationId && !rows.some((row) => row.id === observationId)) observationId = null;
    const key = JSON.stringify([radio, nodeId, observationId, rows]);
    if (key !== rowsKey) {
      rowsKey = key;
      const content = document.createDocumentFragment();
      for (const row of rows) {
        const representative = row.observations.find((item) => textProperty(radio === "wifi" ? item.properties.SSID : item.properties.Name)) || row.observations[0];
        const properties = representative.properties;
        const tr = element("tr", row.id === observationId ? "selected" : "");
        tr.dataset.observationId = row.id;
        const title = element("button", "observation-select", recordName(properties));
        title.type = "button";
        title.setAttribute("aria-pressed", String(row.id === observationId));
        title.title = recordName(properties);
        tr.addEventListener("click", () => {
          observationId = row.id;
          // Keep the focused button in place, including for keyboard selection.
          for (const existing of $("observations-body").children) {
            const selected = existing.dataset.observationId === observationId;
            existing.classList.toggle("selected", selected);
            existing.querySelector("button").setAttribute("aria-pressed", String(selected));
          }
          rowsKey = JSON.stringify([radio, nodeId, observationId, rows]);
          renderDetails(rows, node);
        });
        const nameCell = element("td");
        nameCell.append(title);
        const addressCell = element("td", "address", address(properties));
        if (radio === "bluetooth" && textProperty(properties.AddressType)) addressCell.append(element("span", "address-type", properties.AddressType));
        const reporters = element("td");
        for (const item of row.observations) reporters.append(element("span", "reporter", `${nodeLabel(item.node_id)} · ${signalText(item.properties)}`));
        tr.append(nameCell, addressCell, reporters);
        content.append(tr);
      }
      $("observations-body").replaceChildren(content);
    }
    $("empty-state").hidden = rows.length !== 0;
    if (!rows.length) {
      let title = "No current observations";
      let description = "Results appear automatically when a Pi completes a fresh scan.";
      if (node && result.state === "fresh") {
        title = radio === "wifi" ? "No access points discovered" : "No devices discovered";
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
      if (!connected) {
        title = "Waiting for the server";
        description = "This view is outdated. It will recover automatically when the server responds.";
      }
      $("empty-title").textContent = title;
      $("empty-description").textContent = description;
    }
    renderDetails(rows, node);
  }

  function renderDetails(rows, node) {
    const row = rows.find((item) => item.id === observationId);
    $("details-empty").hidden = !!row;
    $("properties").hidden = !row;
    $("detail-state").textContent = !row ? "No selection" : !connected ? "Outdated snapshot" : node && node.radios[radio].state !== "fresh" ? "Retained · not current" : "Fresh observation";
    if (!row) {
      detailsKey = "";
      $("properties").replaceChildren();
      return;
    }
    const key = JSON.stringify([radio, row]);
    if (key === detailsKey) return;
    detailsKey = key;
    const content = document.createDocumentFragment();
    for (const item of row.observations) {
      const block = element("section", "reporter-detail");
      block.append(element("h4", "", `${nodeLabel(item.node_id)} · ${signalText(item.properties)}`));
      block.append(element("p", "", item.path));
      const properties = element("dl", "properties-list");
      for (const [name, value] of Object.entries(item.properties).sort(([left], [right]) => left.localeCompare(right))) {
        const property = element("div", "property");
        const displayName = name === "Strength" && radio === "wifi" ? "Strength (%)" : name === "RSSI" ? "RSSI (dBm)" : name === "Frequency" ? "Frequency (MHz)" : name === "MaxBitrate" ? "MaxBitrate (advertised Kb/s)" : name;
        property.append(element("dt", "", displayName), element("dd", "", valueText(value)));
        properties.append(property);
      }
      block.append(properties);
      content.append(block);
    }
    $("properties").replaceChildren(content);
  }

  function render() {
    if (!snapshot) return;
    if (nodeId && !selectedNode()) nodeId = null;
    const node = selectedNode();
    const freshNodes = snapshot.nodes.filter((item) => item.radios[radio].state === "fresh");
    $("online-count").textContent = connected ? `${snapshot.nodes.filter((item) => item.state === "online").length} / ${snapshot.nodes.length}` : "—";
    $("observation-count").textContent = connected && freshNodes.length ? snapshot.all_observations[radio].length : "—";
    $("observation-unit").textContent = radio === "wifi" ? "Current access points" : "Current observations";
    $("radio-note").textContent = radio === "wifi" ? "Wi-Fi shows nearby access points, not connected phones or laptops. Signal quality is a percentage. Shared SSIDs can belong to different access points." : "Bluetooth shows discovery observations, including Classic and BLE. Rotating addresses may appear separately. Signal strength is in dBm and does not measure distance.";
    for (const name of ["bluetooth", "wifi"]) {
      $(`radio-${name}`).classList.toggle("active", radio === name);
      $(`radio-${name}`).setAttribute("aria-pressed", String(radio === name));
    }
    $("export-button").disabled = false;
    $("export-button").title = node ? `Export ${node.label}'s ${radioName()} snapshot, including freshness and retained results` : `Export the displayed ${radioName()} groups and reporting measurements`;
    renderBuilding();
    renderNode(node);
    renderRows(visibleRows(), node);
  }

  function updateConnection() {
    $("connection").textContent = connected ? "Server connected" : "Server unavailable";
    $("connection").className = `connection ${connected ? "connected" : "disconnected"}`;
    $("connection-banner").hidden = connected;
    $("connection-banner").textContent = snapshot ? "Connection lost. The last received view is outdated; counts are unavailable until the server responds. Retrying automatically." : "Cannot reach the dashboard server. Waiting for a response and retrying automatically.";
    $("last-updated").textContent = receivedAt ? `Last received ${receivedAt.toLocaleTimeString()}${connected ? " · updates every 2 seconds" : " · outdated"}` : "Awaiting server response";
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
      clearTimeout(deadline);
      inFlight = false;
      updateConnection();
      render();
      pollTimer = setTimeout(poll, document.hidden ? 10000 : 2000);
    }
  }

  for (const name of ["bluetooth", "wifi"]) {
    $(`radio-${name}`).addEventListener("click", () => {
      radio = name;
      observationId = null;
      render();
    });
  }
  $("all-observations").addEventListener("click", () => {
    nodeId = null;
    observationId = null;
    render();
  });
  $("export-button").addEventListener("click", () => {
    if (!snapshot) return;
    const node = selectedNode();
    const payload = {
      exported_at: new Date().toISOString(),
      snapshot_received_at: receivedAt.toISOString(),
      outdated: !connected,
      radio,
      scope: node ? { type: "node", node_id: node.id, label: node.label, floor: node.floor, location: node.location } : { type: "all_observations", note: "Grouped results fresh at the time of the last received snapshot" },
      nodes: (node ? [node] : snapshot.nodes).map((item) => ({
        id: item.id, label: item.label, floor: item.floor, location: item.location,
        ip: item.ip, state: item.state, age: item.age, heartbeat_age: item.heartbeat_age,
        radio_state: item.radios[radio].state, radio_age: item.radios[radio].age, radio_error: item.radios[radio].error,
      })),
      observations: node ? visibleRows().map((row) => {
        const { path, properties } = row.observations[0];
        return { path, properties };
      }) : visibleRows(),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = element("a");
    link.href = url;
    link.download = `nearby-${radio}-${nodeId || "all"}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      clearTimeout(pollTimer);
      poll();
    }
  });
  poll();
})();
