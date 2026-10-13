# Architecture

```
sitesizer/
  core/        pure Python (pydantic + stdlib), no Qt
    catalog.py   schema + validation of data/catalog.json, FortiOS-dependent limits, compat overlay
    models.py    SiteInput (user input), SiteResult, BomLine, Check, Power/Rack/IP summaries
    sizing.py    size_site(): reserve → switch categories → core → firewall ladder → BoM → power/rack → checks
    passive.py   closets (MDF/IDF), house cabinet pattern, fibre links (ODF at both ends), manual layout
                 overrides (anything may be deleted), device names, Corning parts
    location.py  a location = several floors: one firewall/core on the firewall floor, results added up
    passive_calc.py  passive parts of existing cabinets read from an Excel file or typed in (passive_count.py)
    template_import.py  company Excel template → catalog (codes, names, units, two prices, sections)
    ipplan.py    subnet sizing (prototype formula) and aligned, non-overlapping carving
    pricing.py   subtotal / discount / VAT;  compare.py  scenario diff
    project.py   multi-location project files (*.sizing.json, atomic save)
    presets.py   one-click templates (data/presets.json)
    report.py    rows/CSV/TSV/JSON views of a result, specification sections in template order
    ap_wiring.py which Wi-Fi switch port each AP goes to; neighbours (row / column) alternate switches
    topology.py  device-level network (FortiGate HA → MCLAG core → access stacks) from the cabinets,
                 the user's TopologyEdits, automatic layout and orthogonal link routes
  exporters/   Qt-based rendering without widgets (works in a worker thread and from the CLI)
    diagram.py   SiteDiagram / ProjectDiagram QPainter renderers → screen, PNG, SVG, PDF, Excel
    rack.py      RackDiagram: front elevation of every cabinet (same renderer for screen and exports)
    topology.py  TopologyDiagram: painter of core.topology (editor, PNG/SVG, Excel, PDF), hit tests
    ap_wiring.py ApWiringDiagram (AP plan coloured by switch + port cards) and the Excel table
    drawio.py    the same topology as an editable .drawio file (stacks = containers, glued edges)
    xlsx.py      openpyxl workbook: Слаботрумка (template), Схема+шафи (cells), IP, prices, inputs
    pdf.py       QPdfWriter + QTextDocument report, paginated with header/footer
  gui/         PySide6 widgets only — no business rules
    state.py     AppState: project, current site, debounced recompute, undo stack, settings
    lazy.py      when_shown(): hidden pages refresh when they are shown (keeps typing fast)
    theme.py     design tokens (light/dark), type scale, generated QSS, palette
    icons.py     Lucide SVG icons recoloured per theme
    widgets/     controls (stepper, toggle, segmented, chips, callouts), shell (sidebar, top bar, summary bar),
                 feedback (toasts, checks panel), overlays (dialogs, command palette, tour), diagram views
    views/       location, racks (telecom rooms → cabinets, drag & drop editor), bom (inline edits), ipplan (VLAN edits), topology, power,
                 compare, projects, catalog_view (template import), settings_view, help_view
    widgets/rack_editor.py  interactive cabinet canvas; widgets/topology_canvas.py  editable network diagram; widgets/export_dialog.py  export choices
    main_window.py, app.py (entry point, logging, crash safety), workers.py (background exports)
  i18n/        uk.json / en.json + Translator (formatting, Ukrainian plural forms)
  data/        catalog.json (default, datasheet-verified), presets.json
  cli.py       console flow compatible with the prototype + --json automation
tests/         engine, golden (from the original prototype), IP planner, catalog/project, exporters, GUI smoke
tools/         screenshots.py (offscreen GUI captures for docs and visual review)
packaging/     PyInstaller spec, icon and version-info generators
```

## Data flow

```
user edit ─► AppState.edit() ─► QUndoCommand (mergeable per field) ─► SiteInput (validated)
         ─► 60 ms debounce ─► core.location.size_location(floors) ─► size_site() per floor ─► SiteResult
         (current floor) + combined SiteResult (whole location: BoM view, IP plan, exports)
         ─► resultChanged ─► every view re-renders (BoM, diagram, IP plan, power, summary bar)
```

- The engine is a pure function: deterministic, side-effect free, and well under 1 ms for typical sites, so it
  runs on every change.
- Every number the engine uses comes from the catalog (`models`, `categories`, `firewall_ladder`, `tiers`,
  `rules`). The GUI never decides anything about equipment.
- User-facing strings are built in the engine through the i18n layer, so Excel, PDF, CLI and GUI read the same.
- `prototype_compat_catalog()` overlays the old console script's assumptions (quantity-based variants, the legacy
  50/150/45 W power estimate, old FortiLink limits). The golden tests use it to show the engine still reproduces
  the original script's results (`tests/golden/prototype_scenarios.json` was generated by running
  the console prototype `counter_location.py`, removed since; it is in the git history).

## Sizing steps (core/sizing.py)

1. **Reserve** — each AP zone, sockets, cameras and expected clients are scaled by (1 + reserve) and rounded up.
2. **Dual-PSU decision** — `redundant_psu` (None = tier suggestion + confirmation prompt).
3. **Categories** — Wi-Fi (24 APs/switch), access (48 sockets/switch), CCTV (48 cameras/switch); variant chosen
   by `rules.variant_mode`. Wi-Fi: 802.3bt port check → auto-upgrade; PoE load → auto-add switches; warnings.
4. **Core** — auto/yes/no; N+1 pair per tier; capacity check against the core's port count (ICL + FortiLink
   uplinks + dual-homed access links).
5. **Firewall ladder** — first model passing the FortiSwitch limit (FortiOS-dependent), the FortiAP limit, 10G
   ports when a core exists, and the optional threat-protection throughput with headroom.
6. **BoM** — equipment lines with reasons, details and tags; tier lines (UPS, OOB); add-ons (transceivers,
   cabling, rack, licences, spares, management); SLA reference row.
7. **Manual edits** — quantity overrides are applied to the switch categories *before* racks and power, so
   a changed switch count re-plans everything; price overrides and hand-added lines are applied to the BoM.
8. **Power, passive & racks, IP plan, general checks** (`passive.plan_passive` lays out cabinets and counts cabling) (lifecycle, unverified data, IDF, dual WAN, density hints).

## GUI design system

- Tokens (`gui/theme.py`): `bg`, `sidebar`, `surface`, `surface_alt` (#EAF0F6), `border`, `border_strong`
  (#B7C4D1), `text` (#1F2D3A), `text_muted`, `primary` (#1F4E78), desaturated `success`/`warning`/`error`/`info`
  with tinted backgrounds, and per-category colours. A dark set mirrors every token.
- Type: Inter (bundled, SIL OFL) with a 12/13/15/18/24 px scale; 8 px spacing grid; radius 6 (inputs) /
  10 (cards); motion ≤ 180 ms (segmented pill, toggle knob, toasts).
- The QSS is generated from the tokens; custom-painted widgets read the same tokens and repaint on
  `theme_manager.changed`. Icons are recoloured SVGs, so they always match the theme.
- Layout: sidebar (navigation + locations) | top bar (title, undo/redo, palette, theme, export) | page |
  persistent summary bar. Below ~1180 px the sidebar collapses to icons; below ~980 px the location page
  becomes a single column.

## Robustness

- Pydantic validation on every catalog/project/site load, with friendly error lists. A broken user catalog
  falls back to the default and shows a toast.
- Atomic project saves (`.tmp` + replace); unsaved-changes prompt on close, new project and open.
- Exceptions are logged to a rotating file (`logs/sitesizer.log`) and shown as a toast; the app keeps running.
- Signal connections to long-lived state use bound methods, so views that are deleted when the language
  changes and the UI is rebuilt disconnect automatically.

## Racks and manual layout (core/passive.py)

1. Switches are assigned to closets. With endpoints entered per telecom room (`SiteInput.rooms`, Wi-Fi zones
   with `room`) every room is sized on its own (`CategoryResult.per_room`) and its switches stand in its own
   cabinets, with the model chosen for that room (`RoomInput.models`); otherwise they are spread over the
   closets (MDF + IDFs when the longest run exceeds 90 m). Ajax / СКУД / other devices take video switch ports.
2. Each closet is laid out top-down: fibre panels (ODF), FortiGate/core, then the switches in the order
   WSW · VSW · ASW (`RACK_ORDER`), each in the house pattern
   `organizer · panels · organizer · switch · organizer · panels …` (a 48-port switch gets one panel above
   and one below), PDUs and UPS at the bottom. When a closet needs several cabinets, the first one holds the
   firewall, the core and the PoE switches and the access switches go to the others (`_split_poe`) — unless
   that needs more cabinets than a plain greedy fill; then the cabinets are filled and balanced by switches.
3. `SiteInput.layout` is applied: removed/added cabinets, per-cabinet floor/letter/size/name, items moved by
   the user (exact unit), hidden passive items and extra organizers/panels/shelves. Automatic items that collide
   with a manual one move to the nearest free slot; conflicts become `RACK_LAYOUT` checks.
4. Names are assigned top-down per cabinet: `<code>-<floor><letter>-ASW01`, `ПП №1`, `ПП №V1`, `ПП Wi-Fi`.
5. Fibre: a link that ends at the firewall's cabinet (or goes to the firewall floor) is a `fiber_fw_fibers`
   (24) cable with 24-fibre housings, any other one a `fiber_rack_fibers` (12 or 6) cable.
6. Uplinks are matched to the final positions: ≤ `dac_short_max_u` apart in one cabinet → 1 m DAC,
   otherwise 3 m; remote closets use fibre.

Every item has a stable id (`access_switch:3`, `panel:access_switch:3:1`, `org:…`, `firewall:1`) so manual
positions survive recalculation.

## Network topology (core/topology.py)

1. Nodes come from the cabinets of the whole location (`combine()` keeps every floor's plans), so names match
   the racks: FortiGates, core switches, and the access switches grouped into columns (stacks) per cabinet and
   class — PoE (WSW, VSW) apart from ASW — at most `rules.topology_group_size` each (editable on the page).
2. Links: WAN → each FortiGate, HA between FortiGates, ISL (MCLAG ICL) between cores, every FortiGate to every
   core. Access: `ring` — a column is daisy-chained, its top switch to core 1 and its bottom one to core 2;
   `dual` — every switch to both cores. Without a core the FortiGates take that role. Labels: speed and
   medium (DAC in the same cabinet, the fibre type between cabinets). Core / FortiGate port counts are checked.
3. `SiteInput.topology` (`TopologyEdits`, a location-wide field) applies the user's changes: positions,
   removed / added links, link types, labels, switches moved to another stack.
4. `route_links()` routes orthogonally: up-links of the top switch leave upwards, the others run up a lane
   beside their column and sideways in a band above the columns; ports along a side are spread. The painter
   and the draw.io writer use the same routes and label positions.

## Equipment chosen by the user

- `SiteInput.firewall_model / firewall_count / core_model / core_count` (location-wide): replace the ladder's
  FortiGate and the calculated core; 0 = none. A model over its FortiLink / FortiAP limit is a warning.
- `RoomInput.counts` (switches per role typed for a room), `ups_model / ups_count` (UPS of the room's first
  cabinet; `None` = main room by tier / add-on), `devices` (catalog devices placed under the firewall / core).
- A catalog FortiGate or switch put into a cabinet by hand (`RackExtra`, role from `role` or
  `passive.device_roles`: model kind, category models, `…-DSW01`-style names) gets the role's group, so it is
  named, counted for power and the firewall, listed once in the specification and drawn in the topology.
- `RackProps.room` moves any cabinet to another telecom room.
