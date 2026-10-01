# UI/UX development plan

Date: 2026-10-01. Branch: `development/building-radio-system`.

Status: **Planned — the interface changes below are not implemented yet.** Baseline: `a58bf92`.

This is the next frontend workstream for [system-plan.md](system-plan.md), following the application research. Start with search/filtering and better-organized details. Deliver small, separately reviewable changes using the stage/checkpoint approach in [development-plan.md](development-plan.md). Physical Pi deployment remains a separate, unfinished workstream.

## 1. What the user should be able to do

1. Open the dashboard and understand which Pis are reporting.
2. Choose Bluetooth or Wi-Fi and see observations across the building or from a particular Pi.
3. Find a device/network by name or address without reading every row.
4. Select an observation and quickly understand its identity, reporting Pis, signal and available information.
5. Recognize an empty scan, old results, radio failure, and an offline Pi.
6. Export exactly the results and scope being inspected.

Keep the headless Pi setup simple: Python server, existing scanner dependency, plain HTML/CSS/JavaScript, system fonts and local SVG. These UI stages need no new pip/npm packages, frontend framework, CDN, database, or build step. The browser runs on the user's PC.

## 2. Current interface and concrete gaps

Source of truth: [building.html](static/building.html), [building.js](static/building.js), [building.css](static/building.css), and [dashboard guide](docs/dashboard.md).

Already implemented:

- Three floor panels, nine configured Pi markers, and per-radio counts.
- Bluetooth/Wi-Fi selection, individual-Pi selection and All observations.
- Shared observations with separate measurements from each reporting Pi.
- Node/radio health, connection-loss messages, JSON export and a mobile layout.
- Full available properties and an independent local Bluetooth GUI.

Gaps to address:

- No text search, user-selected sort, or floor scope.
- Details are one alphabetical property list per reporter; internal object paths appear before useful summaries.
- Live results are sorted by signal on each update, and changed results rebuild the table body. Rows can move and keyboard focus can be lost.
- Much of the current interface uses small text. Controls, secondary text and status labels need a readability review.
- New filters must be reflected in counts, empty messages and exports so the view remains understandable.

## 3. Page layout and visual direction

Keep one dashboard page and the existing calm green/neutral palette. Give operational information more space than the introductory heading. Use colour together with text and a clear selected outline.

Desktop layout after these stages (illustrative values only):

```text
Nearby                         Server connected · Updated just now
[Bluetooth] [Wi-Fi]            Pis online: 8/9       [Export view]

Building                       Observations · All floors
[All floors / Floor 1 / ...]    [Search name or address...       ×]
                               [Sort: Strongest signal] [Sort now]
Floor 3   Pi-07 (6) Pi-08 (—) Pi-09 (0)
Floor 2   Pi-04 (3) Pi-05 (2) Pi-06 (1)   Showing 3 of 24
Floor 1   Pi-01 (4) Pi-02 (2) Pi-03 (5)   Name / Address / Reported by
                               ----------------------------------
Selected Pi health             Selected observation
Location · IP · radio status   Summary · Per-Pi measurements
                               > Services / advertised data
                               > All properties / raw values
```

On a narrow screen, stack controls, building, results and details. Keep radio selection and scope clear. Use wrapping labels and a contained table scroll if needed; avoid horizontal overflow of the entire page. Keep details inline rather than adding a modal/navigation system.

Design targets: approximately 14–16px reading text, at least 12px secondary labels, visible keyboard focus, controls with roughly 44px touch targets, and normal text contrast of at least 4.5:1. Verify the actual combinations used; these are acceptance targets, not a current accessibility claim.

## 4. Behaviour rules shared by every stage

### Scope and search

- Radio selection remains separate: Bluetooth observations and Wi-Fi access points never share a combined device total.
- Initial scope is All observations. Clicking a Pi changes the scope to that Pi; provide an obvious route back.
- Search is a trimmed, case-insensitive substring match over displayed name/alias/SSID and address/BSSID. Treat the query as plain text, not a regular expression. Do not search arbitrary binary/raw properties initially.
- Within an aggregated row, matching any in-scope reporter's name/address includes that row and all its in-scope reporters. Explain this in the search help.
- Empty query shows all eligible rows. Keep the query when switching Pi, floor or radio so repeated investigation is convenient; show the active query and a Clear search action.
- Keep the existing fresh-only All observations rule. A selected Pi may show retained observations with explicit stale/error/offline context. Do not introduce an ambiguous global “include old devices” switch in the first iteration.

### Counts and freshness

- Pi badges retain their latest scan counts for the selected radio; text search does not change them. Label this clearly beside the map.
- Building totals remain building-wide; label them accordingly. The result list shows `Showing X of Y` for its current scope and search.
- A fresh empty scan is `0`. Stale, error, offline, waiting or disabled data has no current count (`—`). Filtering to no matches must not imply the scanner found nothing.
- Use **Last successful scan received** or **Scan report age** for `node.radios[radio].age`. It is not an exact per-device sighting time. Keep Last contact and Last heartbeat separate.
- A shared observation retains each Pi's signal and scan-report age. Do not invent a single device age or combine different reporters' properties into one authoritative record.
- Keep Bluetooth RSSI in dBm and Wi-Fi signal quality in percent. Unknown signal sorts after known signal. Missing values stay Unknown; hidden SSID stays Hidden network; raw SSID bytes remain available.
- Pi markers describe configured scanner locations. A detection does not establish a device's floor, distance or precise position.

### Stable updates and exports

- Poll at the existing 2-second interval, or 10 seconds in a hidden tab, with no overlapping requests.
- Preserve search input/caret, focused controls, selected observation, scroll position and expanded detail sections when an update retains the same records.
- Default to strongest signal when entering a scope. Offer Name and Address sorting, with a stable identity tie-breaker. Apply sorting on an explicit search/scope/sort change or Sort now; ordinary polls update values in place, append newly appearing rows and remove vanished rows without continually reordering existing rows. Explain that order is refreshed with Sort now.
- Keep order/expansion state bounded to current records and the selected observation. Do not accumulate browser history of detections.
- If a selected row leaves the scope, no longer matches, or disappears from current results, clear its details with a short explanation; do not leave an apparently current stale selection.
- Export uses the same derived rows as the table. Include radio, scope, query/sort settings, snapshot receipt time, outdated flag and relevant node/radio health. Preserve raw property values and per-Pi measurements.
- Keep existing export field meanings; add filter metadata. Document the deliberate change that Export view now exports the filtered set. A valid empty filtered export is an empty observation list with its scope/filter context.
- During disconnection, retain the view and filters, mark the snapshot outdated and suppress current counts. Any export must retain `outdated: true`.

## 5. Stage UX-1 — Find observations quickly

**First implementation checkpoint.** Add search and explicit sorting to the current result panel while keeping the building view familiar.

Work:

- Add a labelled search input, Clear search, sort selector, Sort now and matching-result count.
- Reuse Pi and radio selection as the first scope filters.
- Separate small functions for obtaining scoped rows, matching, ordering, rendering and export. Keep these in `building.js`; introduce another local file only if it materially improves readability.
- Update existing rows by stable IDs so polling does not replace a focused row or interrupt typing.
- Add a distinct No matching observations message with Clear search, while preserving real empty/error/waiting messages.
- Update filtered-export behaviour in the same change, so a filter cannot silently disagree with the downloaded data.

**Files:** `static/building.html`, `static/building.js`, `static/building.css`, `tools/check_browser.mjs`, `docs/dashboard.md`.

**Checks UX1:** search by partial name and address; mixed case; surrounding whitespace; unnamed devices; hidden SSIDs; a name present only on another reporter; literal HTML-like text; zero matches; switching radio/Pi with an active query; correct filtered export; matching counts; stable focus, selection and order across changing signals. Verify selection clears correctly when a result disappears.

**Review demo:** find one known synthetic observation, narrow to a Pi, clear the query, change sort, watch several updates, and export the filtered view. One independently testable commit.

## 6. Stage UX-2 — Make device details understandable

**Second implementation checkpoint.** Keep the full data available while making the first visible information useful.

Work:

- Add a short summary above the detailed properties: name/SSID, address/BSSID and radio type.
- Show reporting Pi, location, signal, radio state and scan-report age together. If reporters disagree on a name or property, keep each reported value visible in that Pi's section.
- Bluetooth groups: Identity; Signal and status; Advertised services; Manufacturer/service data.
- Wi-Fi groups: Identity; Signal; Frequency/channel; Advertised security/capabilities.
- Show Paired/Connected only as reported Bluetooth properties; explain that they describe the reporting adapter's relationship, not building-wide presence.
- Use native expandable sections for longer data. Keep All properties available with original names, binary hex values and object path; move that technical path out of the headline summary.
- Use a small local display-label mapping. Company-name databases and service-name lookup packages are later work.
- Keep the table compact. In Wi-Fi mode show channel and advertised security as secondary text where space permits; full per-reporter values remain in details.

**Files:** the three building assets, browser checks and dashboard guide.

**Checks UX2:** missing and false/zero values; long and Unicode names; nested manufacturer/service data; shared observations with different signals/names; same SSID with different BSSIDs; retained stale details; raw/export values unchanged; expanded sections and keyboard focus survive updates.

**Review demo:** select one Bluetooth observation and one access point, compare two reporting Pis, then expand the original properties. Show a radio error and its retained details. Separate commit from UX-1.

## 7. Stage UX-3 — Make building navigation clearer

Add an All floors / Floor 1 / Floor 2 / Floor 3 scope selector once search and details work well.

- All floors keeps the existing three-floor overview. A selected floor focuses the map and list on that floor's Pis.
- Clicking a Pi selects that Pi and its floor. Changing floor clears the selected Pi/observation. All observations returns to all Pis within the chosen floor; All floors is the route back to the full building.
- For floor-scoped aggregated results, remove out-of-scope reporters **before** matching, sorting, counting, details and export. Drop groups with no remaining eligible reporters. Keep existing identity/grouping rules.
- Add a compact list of Pis needing attention next to the map: offline nodes or enabled radios in waiting/stale/error state. Clicking an entry opens the Pi; show the affected radio explicitly. Disabled radios are labelled but are not failures.
- Add a clear selected-Pi heading, location and both radio states. Keep all nine configured markers reachable even when there are no observations.

**Files:** building assets, browser checks and dashboard guide; no report/API schema change is expected.

**Checks UX3:** every floor and Pi; return to full building; cross-floor shared device; a name matching only an excluded reporter; strongest signal outside the selected floor; search combined with scope; floor export; failed Pi does not hide others; fresh-zero and offline remain different.

**Review demo:** inspect three Pis on Floor 2, find a shared observation, compare their measurements and open an offline Pi from the attention list. Separate commit.

## 8. Stage UX-4 — Readability, keyboard use and final walkthrough

Polish the completed interactions without adding another feature set.

- Reduce oversized introductory copy; improve text size, contrast, spacing and consistent status labels.
- Ensure each control has a visible label or accessible name. Use real buttons/inputs/selects and a logical keyboard order. Announce meaningful state changes without reading the entire table every poll.
- Test keyboard search, clearing, scope/radio/sort changes, marker selection, details and export. Focus must stay visible and predictable.
- Review 1440px desktop, 768px tablet, 390px phone and 200% browser zoom. Check long node labels, long addresses/data, touch targets and page overflow.
- Exercise a synthetic 500-row view, recording responsiveness and browser behaviour on the test PC. Confirm repeated updates do not accumulate rows, handlers or retained UI state. This is not a Pi hardware benchmark.
- Preserve the existing local Bluetooth GUI, served separately from the building dashboard.

**Checks UX4:** all preceding browser cases; empty/waiting/error/stale/offline/disabled states; disconnection/reconnection while typing or inspecting details; hidden-tab polling; keyboard walkthrough; no external asset requests or JavaScript errors; local GUI regression.

**Review demo:** complete the user journey in section 1 with only the keyboard, repeat on the narrow layout, then interrupt and restore the demo server. Record automated and manual evidence separately.

## 9. Repeatable validation and progress

Extend the existing browser harness using deterministic synthetic reports. It already starts temporary servers and checks the building dashboard and local GUI without radio hardware. Node and Chrome are development tools only.

```bash
node --check static/building.js
node --check tools/check_browser.mjs
node tools/check_browser.mjs
git diff --check
```

Run the relevant browser checks after each behavioural stage. Pure layout changes get visual and keyboard checks. Run backend tests if backend/API files change; do not change the scanners or registry merely to support presentation. Reuse the isolated demo instructions in [docs/dashboard.md](docs/dashboard.md).

Each stage gets its own implementation commit, verification summary and repeatable demonstration on the development branch. Update this table and [development-progress.md](development-progress.md) from observed evidence, then push the checkpoint. Keep checkpoints easy for the owner to inspect; the plan does not require repeated permission questions or a single large redesign.

| Stage | Status | Evidence / commit |
| --- | --- | --- |
| UX-1 Search, sort and reliable filtered views | TODO — start here | |
| UX-2 Organized summaries and full details | TODO | |
| UX-3 Floor scope and Pi health navigation | TODO | |
| UX-4 Readability and complete walkthrough | TODO | |

Done means the requested interaction works, its relevant checks pass, its demonstration is repeatable, and the checkpoint is recorded. This plan file is not evidence that those UI changes or physical Pi tests have passed.

## 10. Later features and research references

Keep signal-history charts, heatmaps, estimated device positions, editable floor plans, pairing/connections, remote fleet controls, persistent history, saved filters/favourites, login redesign and themes outside these stages. Add them only after the core interface has been used and a concrete need is identified.

References from the app comparison, used for interaction ideas:

- [LightBlue](https://punchthrough.com/lightblue/): readable device lists and a prominent search entry point.
- [nRF Connect scanner documentation](https://github.com/nordicsemi/Android-nRF-Connect/blob/main/documentation/README.md): filters and expandable advertisement/raw details.
- [NetSpot Inspector](https://www.netspotapp.com/help/what-is-inspector-mode/): searchable/sortable lists and separate detail views.
- [Kismet device views](https://www.kismetwireless.net/docs/api/device_views/): organizing observations by source and radio.
- [ESPresense Companion](https://espresense.com/companion/): floor navigation and labelled node placement; its device-position estimation is a separate feature.

Adapt those interaction patterns to the existing lightweight application; preserve the scanner/agent/server separation and the freshness rules in the system plan.
