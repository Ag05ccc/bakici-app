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

## 11. [opus] Review findings and proposed additions

Source: interface review on 2026-10-01 at `b9e0e9a`, using the nine-node demo (`--error-radio pi-08:wifi --empty-radio pi-09:bluetooth`), screenshots at 1440px and 390px, and contrast calculated from the colours in `building.css`. Items tagged **[opus]** are proposals only. They join a stage when the owner moves them into that stage and the status table above.

### [opus] Observed gaps

- Small, low-contrast text: 41 of 60 `font-size` declarations in `building.css` are 11px or smaller, down to 8px. Measured contrast below the 4.5:1 target: address type 2.5:1 (9px), footer 2.75:1 (10px), building note 2.7:1 (9px), marker state 3.0:1 (8px), table headers 3.2:1 (9px), `--muted` secondary text 4.05:1 (10–11px).
- The introductory heading takes about 230px above the controls, while connection state and last-received time sit in the header corner and the footer.
- Shared observations list one line per reporter; the demo shared beacon row is about 165px tall with 8 reporters.
- Details render below the table in the same card. Selecting a row requires scrolling, and the table (390px) and properties (370px) each scroll inside the page. Identical properties (Address, AddressType, Name) repeat for every reporter, and every value uses monospace.
- Retained results from a failed radio (Pi 08 Wi-Fi error) use the same selected-row styling as fresh results; the summary reads `— current · Scan error`.
- At 390px the building map is about 750px tall before any result appears, and MAC addresses wrap mid-octet.
- `building.css` contains 78 distinct hard-coded colour values.

### [opus] Proposed additions

Quick wins (CSS only):

1. Type scale: 14px body text, 12px minimum; fix the failing combinations above.
2. Replace hard-coded colours with custom properties on `:root`, so contrast fixes happen in one place and later themes stay cheap. Themes themselves remain outside these stages.
3. Replace the introductory heading with a sticky top bar: radio switch, Pis online, `Live · updated 2s ago`, Export.
4. Keep MAC/BSSID values on one line; use a download icon on Export instead of `↗`.
5. Give stale, error, offline and waiting each a distinct icon/shape plus text; the legend currently merges them into two colour pairs.

Layout:

6. Desktop three-column layout: compact map | result list | sticky details, with no nested scroll regions.
7. Reduce floor-plan height; on narrow screens show the nine Pis as a compact 3×3 grid so results are reachable without scrolling past the map.
8. Show scope as a breadcrumb (`Building › Floor 3 › Pi 08 ✕`) instead of the All observations toggle button.

Interaction:

9. Link list and map: hovering or selecting an observation highlights the Pis that reported it and shows each Pi's signal on its marker. Uses existing per-Pi measurements and does not estimate position (section 4).
10. Compact reporter cell: `8 Pis · best −43 dBm (Pi 01)` plus signal bars, keeping dBm/percent units visible; the full per-Pi list moves to details.
11. Comparison details: show properties identical across reporters once, and a per-Pi table only for values that differ. Every reported value stays visible; raw All properties stays in a collapsed section.
12. A `3 new · Sort now` indicator consistent with the no-reorder rule, plus a brief highlight for appended rows.
13. Keep radio, scope, query and selection in the URL hash so refresh, bookmarks and Back keep the view. No storage; this is not the deferred saved-filters feature.
14. Keyboard shortcuts: `/` search, ↑/↓ rows, `Esc` clears selection, `b`/`w` radio. Shortcuts do not fire while typing in an input.
15. Amber retained-results banner in the list plus dimmed rows when a Pi's selected radio is not fresh; replace the `— current · …` summary wording.
16. Make Pis online clickable to open the UX-3 attention list; show it in amber when anything needs attention.
17. CSV export beside JSON, using the same derived rows and filter metadata.

### [opus] Suggested placement

| Proposals | Suggested stage |
| --- | --- |
| 1–5 | New UX-0 before UX-1, so later stages are not built on 9px text; UX-4 keeps the final walkthrough |
| 12–14 | UX-1 |
| 6, 10, 11, 15 | UX-2 |
| 7–9, 16 | UX-3 |
| 17 | With the UX-1 export change, or later |

## 12. [codex] Feedback on Opus's review

Reviewed 2026-10-01 against the current building assets and registry. Opus's original comments above are preserved. This section adds recommendations and implementation boundaries; it does not mark any proposed UI change as implemented or alter the stage-status table.

### [codex] Verified findings

The readability concern is supported by the source. There are 60 fixed-pixel `font-size` declarations, 41 at 11px or smaller, plus one additional `clamp(...)` declaration. I also counted 78 unique hexadecimal colour literals. Calculating contrast for the stated foreground/background pairs reproduces Opus's results: address type 2.52:1, footer 2.75:1, building note 2.72:1, marker state 2.97:1, table headers 3.17:1, and `--muted` on white 4.05:1. These are checks of those colour pairs, not a complete accessibility audit.

The code also confirms separate fixed-height table/property scrolling, repeated per-reporter property lists, and full row replacement when observation data changes. The quoted screenshot dimensions remain Opus's visual measurements; I did not independently remeasure them for this review.

### [codex] Proposal-by-proposal feedback

| Opus item | Recommendation | Boundary / acceptance detail |
| --- | --- | --- |
| 1. Type scale and contrast | Agree; do this before UX-1. | Use the proposed 14px reading size and 12px minimum for secondary text. Check normal, selected, hover, retained and disconnected states after the change. Larger text must not force addresses or buttons outside the viewport. |
| 2. Colour variables | Agree; keep it small. | Introduce semantic variables for text, surfaces, borders, focus and health states. Consolidate repeated purposes; avoid turning all 78 literals into 78 independent settings or building a theme system. |
| 3. Compact sticky top bar | Agree with a shorter header; qualify the status wording. | Prefer `Server connected · Dashboard received 2s ago`. A successful dashboard poll does not prove that any Pi or radio has fresh results. Preserve separate scan ages. Sticky controls must not cover focused content at narrow widths or 200% zoom. This includes HTML/JS work, not just CSS. |
| 4. Addresses and export icon | Agree. | Keep each MAC/BSSID together, allowing a contained scroll or more row space at narrow widths rather than shrinking text. Keep the visible Export label and use a download icon; an icon alone is insufficient. |
| 5. Distinct health indicators | Agree. | Keep readable text for Fresh, Stale, Scan error, Offline, Waiting and Disabled. A shape/icon supplements the text; it must not replace it. Server disconnection remains its own state. This also requires markup/rendering changes. |
| 6. Three-column desktop layout | Conditional; try during UX-2 after enlarging text. | Use it only when map, list and details remain readable; keep a two-column/stacked fallback. Do not make a tall sticky detail panel's bottom unreachable. Reduce competing vertical scrollers; a contained horizontal scroll for large raw values can remain. |
| 7. Compact mobile map | Agree with a floor-labelled overview. | Use three labelled floor groups ordered 3, 2, 1, with three Pis in each. Treat a compact grid as a navigation overview, not a representation of configured x/y positions. Keep an expandable building diagram available so actual configured placement can still be inspected. |
| 8. Scope breadcrumb | Agree for UX-3. | `Building` means all floors; `Floor 3` means all eligible Pis on that floor. Give the close action the accessible name Clear Pi selection and return it to the floor view. Align this with the floor selector's state. |
| 9. Linked map/list | Agree for selected observations first. | Highlight in-scope reporting Pis on mouse or keyboard selection. Keep signal separate from the scan-count badge, and mark retained measurements as old. Do not move markers or imply device position. Hover-only behaviour is optional later. |
| 10. Compact reporter cell | Strong agreement for UX-2. | Use `8 Pis · strongest −43 dBm · Pi 01`; count distinct Pi IDs, not the number of records in the group. Compute the strongest known value only from in-scope reporters. Preserve each measurement in details; bars are optional, and Unknown must not become zero. |
| 11. Shared-property comparison | Agree with a limited first version. | Display an identity field once only when every in-scope reporter explicitly supplies the same value. Keep conflicting or missing values per Pi. Preserve distinctions between absent, empty, false and zero. Leave nested manufacturer/service data in per-Pi expandable sections; a generic comparison engine is unnecessary. Never merge pairing/connection or freshness into a building-wide device status. |
| 12. New-row indicator | Agree for UX-1 if the definition is explicit. | It counts matching rows appended since the last explicit sort, not newly discovered physical devices. Exclude the initial load, remove vanished rows from the count, and reset on query/scope/radio changes or Sort now. Any highlight must respect reduced-motion preferences. |
| 13. URL hash state | Useful; defer to a separate checkpoint after UX-3. | Stabilize scope/filter behaviour first. Validate restored values, handle removed Pis, and avoid adding a history entry for every keystroke or poll. Persist view controls only initially; a removed observation must not reappear as a current selection. |
| 14. Keyboard shortcuts | Move to UX-4; native controls come first. | Tab, Shift+Tab, Enter and Space should already work in each stage. Consider `/` and contextual Escape later. Defer global arrow interception and `b`/`w`; shortcuts must ignore editing controls, contenteditable, modifier combinations and text composition. |
| 15. Retained-results banner | Strong agreement; add in UX-2. | Prefer `Wi-Fi scan failed · showing previous results` with the actual scan-report age. Use a labelled banner/background treatment rather than reducing text opacity below the contrast target. Do not describe every non-fresh state as an error; never-reported radios have no retained results. |
| 16. Clickable Pis online | Agree with separate connectivity and attention counts. | Use a real button. `9/9 online` may coexist with `1 radio needs attention`; show both facts rather than letting an amber online number imply a Pi is offline. Open the attention list without changing observation scope until an item is selected. |
| 17. CSV export | Defer; keep JSON in UX-1. | CSV needs its own flat schema, defined rows per reporting Pi/observation, scope/freshness fields, quoting and spreadsheet-safe handling of device-supplied text. It should not delay making the existing JSON export match the visible filtered rows. |

### [codex] Recommended implementation order

1. **UX-0: readability foundation.** Adopt the small font/contrast/colour-variable/address/icon/health-label fixes from items 1, 2, 4 and 5, plus a shorter heading. Check desktop, 390px, zoom and all health states. Keep the full sticky-toolbar restructuring out of this first small change.
2. **UX-1: search, stable rows, explicit sorting and matching JSON export.** Keep the existing scope and include item 12's bounded new-row indicator. Focus must survive polling; preserving it only during an initial click is insufficient.
3. **UX-2: compact reporters and readable details.** Prioritize items 10, 11 and 15. Try item 6 only at widths where the larger type still fits. Keep every per-Pi value available.
4. **UX-3: building navigation.** Add floor scope, breadcrumb, compact mobile overview, selection-to-map highlighting and the attention list from items 7–9 and 16. Test a shared observation reported from several floors to catch out-of-scope data leakage.
5. **UX-4: complete accessibility and interaction walkthrough.** Recheck the earlier improvements, finalize toolbar behaviour, and consider only the shortcuts that improve the demonstrated workflow.

URL state and CSV stay later, independently reviewable additions. The first useful change should improve readability without taking on all 17 proposals at once. When consolidating this feedback into the executable plan, update sections 3–9 and the status table together so the implementation has one consistent set of requirements.
