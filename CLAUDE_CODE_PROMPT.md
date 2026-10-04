# TASK: Build a premium desktop GUI app for network equipment sizing (BoM + IP plan + topology diagram)

Take as much time and as many tokens as you need. Quality, usability and visual polish matter far more than speed. Research the domain deeply before writing code, plan first, then build in milestones, and verify your own work (run it, take screenshots of the GUI, fix what looks off).

---

## 0. Who I am and what this is for

I am a network engineer. I size the network equipment for company locations (warehouses, offices, R&D sites, 3D-printing farms, outdoor areas) based on Fortinet gear (FortiGate, FortiSwitch, FortiAP). Given a few numbers about a location (Wi-Fi APs, Ethernet sockets, cameras, criticality, etc.) I need a tool that instantly produces:

1. A justified Bill of Materials (BoM) with a human-readable reason per line (for the customer).
2. Optionally an IP/VLAN plan.
3. A clean topology diagram.
4. Export to Excel (and PDF/PNG).

I currently have a working console prototype (`counter_location.py`, in this folder — **read it first**; it is the reference for current logic). The goal now is to turn it into a **serious, fast-to-use, beautiful GUI application** that I can use daily, and that colleagues can use too.

Environment: Windows 11, PowerShell, VS Code, Python 3.x. UI language: **Ukrainian by default** (all labels, tooltips, reasons in the BoM, exported files). Add an English UI toggle if it is cheap with a proper i18n layer. Code, comments, identifiers: English.

Whiteboard photos of my notes may also be in this folder (equipment catalog + a hand-drawn topology). Read them if present; the transcription is in section 2 (handwriting is hard to read — see uncertainty notes).

---

## 1. Research phase (do this BEFORE coding)

Use web search / docs fetching extensively. Produce a short `docs/RESEARCH.md` with findings and sources. Specifically:

- **Fortinet datasheets / ordering guides** for every model below: FortiSwitch (FS-124G-FPOE, FS-624F-FPOE, FS-448E, FS-148F, FS-448E-POE, FS-148F-FPOE, FS-1024E), FortiAP (FAP-221K, 231K, 241K, 441K, 234G), FortiGate (FG-80F, FG-120G, FG-200G, FG-400G). Verify: port counts, uplink types and counts (SFP/SFP+/SFP28), PoE standards and **real PoE budgets**, power supply options (hot-swap / redundant), typical power draw, rack units, **firewall throughput numbers (firewall / IPsec VPN / IPS / threat protection / SSL inspection)**, **max supported FortiSwitches per FortiGate (FortiLink limits)**, **max managed FortiAPs per FortiGate**, interface types on each FortiGate.
- Where my interpretation below conflicts with official data, **trust the datasheet, flag the discrepancy clearly, and make the value editable in the catalog**. Never silently "fix" my notes — list differences in `docs/RESEARCH.md`.
- **Senior network-engineer best practices** for sizing a site: HA (FGCP active-passive/active-active), core redundancy (stacking / MCLAG), dual WAN / SD-WAN / LTE backup, out-of-band management, uplink oversubscription ratios, PoE budget headroom, VLAN segmentation (data / voice / CCTV / guest / IoT / management), DHCP scope sizing, QoS, cable-length limits (100 m copper → multiple IDF closets), rack space, power and UPS sizing, cooling, spare-parts policy, support/SLA tiers (FortiCare / 24x7), FortiGuard bundle licensing (UTP/ATP/Enterprise), FortiAP/FortiSwitch licensing, FortiManager/FortiAnalyzer sizing, monitoring, transceivers/DAC cables/patch panels/cabling BoM items, and documentation deliverables.
- Research how comparable tools look and work (e.g. vendor configurators, Cisco/Fortinet/Ubiquiti design tools, network-diagram tools) to borrow good UX ideas.
- Research GUI frameworks for a modern Windows desktop app and **choose with justification** (candidates: PySide6/Qt with custom QSS styling, CustomTkinter, Flet, NiceGUI/webview, Tauri/Electron + Python backend). My priorities: gorgeous and calm look, responsiveness, easy to package into a single `.exe` for coworkers (PyInstaller/Nuitka/briefcase), reliable HiDPI rendering, good Ukrainian/Cyrillic font rendering. My lean is PySide6 (Qt) or a strong web-style stack if it truly looks better — you decide after research and tell me why.

---

## 2. Domain data (my notes — treat as the starting catalog, verify against datasheets)

### 2.1 Equipment catalog (transcribed from my whiteboard)

**Aggregation (core) switch:** `FS-1024E`

**Wi-Fi PoE switches** (power the APs):
- `FS-624F-FPOE` — whiteboard note: "25х hot-swap" (**UNCERTAIN meaning**)
- `FS-124G-FPOE` — whiteboard note: "15х" (**UNCERTAIN meaning**)

**Access switches** (user sockets), rule: **1 switch per 48 sockets**:
- `FS-448E` — "25х" (uncertain)
- `FS-148F` — "15х" (uncertain)

**Camera (CCTV) switches**, rule: **1 switch per 48 cameras**:
- `FS-448E-POE` — "25х" (uncertain)
- `FS-148F-FPOE` — "15х" (uncertain)

**Access points:**
- `FAP-221K` — 3D farm
- `FAP-231K` — corridors
- `FAP-241K` — low user density (< 20 clients per AP)
- `FAP-441K` — high user density (> 20 clients per AP)
- `FAP-234G` — outdoor

**Firewalls:**
- `FG-80F` — 1 Gbit, **no SFP+**
- `FG-120G` — 10 Gbit SFP+
- `FG-200G` — same as 120G but manages more switches
- `FG-400G` — same as 200G, even more switches

> **The "25х" / "15х" markers are ambiguous** in my handwriting. They might mean a quantity threshold (e.g. use the hot-swap-PSU variant when switch count ≥ 25), or a port count, or something else. My current prototype assumes "the hot-swap/premium model is chosen when the quantity of that switch type ≥ 25, otherwise the base model". **Make this rule data-driven and editable in the UI**, research what these numbers could realistically mean, and ask me one batched clarification question at the start if still unclear (do not block progress — implement the assumption as default and mark it clearly in the UI/catalog).

### 2.2 Whiteboard topology sketch
Hand-drawn: an HQ site ("HQ-SF") connected to multiple remote sites (green links = WAN/VPN), and a per-location block showing `FG-120G` firewall → `FS-124G-FPOE`, `FS-148F`, `FS-148F-FPOE` switches with endpoints below. Use it as inspiration for the diagram style: simple, hierarchical, readable.

### 2.3 Current calculation logic (from my working prototype — keep these semantics, improve them)

Inputs: location name; number of Ethernet sockets; number of cameras; AP groups (several zones, each with AP type + quantity); location criticality tier (1–4); aggregation switch choice (`y` / `n` / auto); "add 20% growth reserve" (y/n); calculation mode (quick / extended).

Growth reserve: if enabled, multiply sockets, cameras, APs (total and per AP group), and expected Wi-Fi clients by 1.2 and `ceil` BEFORE sizing switches.

Sizing:
- Wi-Fi PoE switches = `ceil(total_aps / 24)` (24 AP ports + uplinks).
- Access switches = `ceil(sockets / 48)` (48 ports + 4×10G SFP+ uplinks).
- Camera switches = `ceil(cameras / 48)`; check PoE budget (740 W assumed for FS-148F-FPOE; ~15 W per camera estimate) and **warn** if close/exceeded.
- Model variant per category is picked by quantity threshold (hot-swap/premium vs base) — see uncertainty above.
- Aggregation switch: `y` → always add; `n` → never add; auto (Enter) → add if total access-layer switches ≥ 3. Aggregation implies the firewall must have 10G SFP+ ports.
- Firewall: pick the smallest model from the ladder (80F → 120G → 200G → 400G) whose FortiLink switch limit ≥ total switches (access layer + aggregation), skipping models without 10G when aggregation is used. Current prototype limits: 80F=16, 120G=32, 200G=64, 400G=128 switches (**verify with datasheets**). If nothing fits, show "need larger model (FG-600G+)".
- Criticality tiers:
  1. **Critical** (DC / main site): firewall HA pair (×2), aggregation redundancy N+1 (×2, stack/MCLAG), UPS line, OOB management link line (4G/5G router), SLA 24x7 ≤4h with on-site spare.
  2. **High** (important regional office): UPS line only; SLA next-business-day with cold spare in warehouse.
  3. **Standard** (typical branch): nothing extra, standard warranty.
  4. **Low** (warehouse / temporary): minimal config, best-effort.
  (Extend this: make tier effects configurable, e.g. dual uplinks, dual PSU requirement, spare-stock percentage, support level.)
- UPS estimation: rough watts = (non-PoE switches × 50 W) + (PoE switches × 150 W base) + (aggregation × 50 W) + (firewalls × 45 W); advise ≥30% headroom (**replace with datasheet-based numbers**, and also account for PoE load).
- Extended mode IP plan: segments Data (sockets), Wi-Fi (expected concurrent clients, or `APs × 15` if unknown), CCTV (cameras), Management (switches + firewalls + APs). Subnet size = smallest prefix fitting `ceil(hosts × 1.3) + 2`. Output prefix, capacity, notes. Improve: let me enter a base network (e.g. 10.50.0.0/16) and auto-carve non-overlapping VLAN subnets with suggested VLAN IDs, gateways and DHCP ranges; add Voice/Guest/IoT optional segments.
- Topology diagram (existing style, keep this clean minimalism): top FIREWALL box (navy `#1F4E78`, white text, "× N (HA)"), CORE box below it (navy, "× N (N+1)"), then a row of light boxes (`#EAF0F6`, border `#B7C4D1`, dark text `#1F2D3A`) for Wi-Fi / Access / CCTV switches with counts, thin connector lines (`#9AA7B4`), muted italic endpoint captions beneath ("24 access points", "10 sockets", "5 cameras"), and a footnote for "+ UPS • + OOB". Only draw categories that exist; skip the core box when there is no aggregation. Calm, flat, no gradients, no neon.
- Output spreadsheet: sheets `BoM` (Category, Model, Quantity, Justification for Customer), `IP-план`, `Схема` (diagram image). Header navy fill + white bold text, thin gray borders, wrapped text, freeze panes, auto-fit widths (cap ~90), and a reference row with criticality level + recommended SLA.

All user-facing text in the prototype is Ukrainian; keep the same tone (clear, professional, customer-friendly justifications).

---

## 3. Product requirements

### 3.1 Core capabilities
- **Instant live recalculation**: change any input → BoM, diagram, IP plan, power/PoE summary update immediately (debounced). No "Calculate" button needed (optional for clarity).
- **Quick vs Extended mode** as a prominent toggle (Quick = equipment only; Extended = + IP plan, VLANs, power/rack/cabling, licensing).
- **Multi-zone APs** (add/remove zones, each with AP type and count), with smart hints (e.g. suggest 441K when density > 20).
- **Criticality tiers** with a visual selector and an always-visible "what this changes" summary.
- **Explain everything**: every toggle/option has an info tooltip or inline caption describing exactly what it adds/changes in the BoM (this replaces the console "manual"). Add a "Why this line?" popover per BoM row.
- **Warnings & validation panel**: PoE budget exceeded, uplink oversubscription, firewall limit exceeded, cable-length/IDF hint, missing inputs, suspicious values. Severity levels (info / warning / error) with calm colors.
- **Best-practice add-ons** (toggleable, each clearly explained, sensible defaults per tier): uplink bandwidth sizing and oversubscription ratio, transceivers / DAC cables, patch panels & cabling estimate, rack units + rack suggestion, UPS sizing with PoE load, licensing/support bundles (FortiCare, FortiGuard), FortiManager/FortiAnalyzer note, spare-parts quantity by tier, dual WAN/SD-WAN/LTE backup, guest Wi-Fi/VLAN, IDF closet count from 100 m limits.
- **Pricing (optional)**: catalog supports optional unit price + currency; if prices are filled, show line totals, subtotal, and optional discount/VAT. Hide price columns when no prices exist.
- **Catalog editor** inside the app (table UI) for models, port counts, PoE budgets, thresholds, FortiLink limits, power draw, prices, tier effects. Stored as human-editable JSON/YAML; ship with a validated default catalog; import/export; reset to defaults; schema validation with friendly errors.
- **Projects**: save/load a location (or several locations in one project — HQ + remote sites, like my whiteboard) as a file; recent projects; duplicate a location; compare two scenarios side by side (e.g. Tier 2 vs Tier 1, with/without reserve) with a diff of BoM and cost.
- **Presets/templates**: e.g. "Warehouse/Logistics", "R&D office high density", "3D farm", "Small branch" — one click fills sensible defaults.
- **Exports**: Excel (formatted, multi-sheet, same style as prototype but polished), PDF report (branded, customer-ready: summary, BoM table with justifications, diagram, IP plan, assumptions & disclaimers), PNG/SVG diagram, JSON/CSV BoM, copy-to-clipboard of BoM as table. Report header with location name, date, author (configurable), optional logo.
- **Topology diagram**: rendered live inside the app, pan/zoom, export as PNG/SVG; multi-location view (hub-and-spoke) for projects; light/dark-theme-aware but the exported version always has a clean white background.

### 3.2 GUI / UX quality bar (this is the most important part)
- **Beautiful, calm, modern, "easy on the eyes"**. Think polished professional tool (like Linear / Notion / Figma-ish restraint, or a good Fluent/Windows 11 look), not a default Tk/Qt gray form.
- Palette: primary navy `#1F4E78`, soft light-blue surfaces `#EAF0F6`, subtle borders `#B7C4D1`, muted text `#7C8894`, dark text `#1F2D3A`; semantic colors for success / warning / error must be desaturated and pleasant. Provide **light and dark themes** (follow system by default, switchable), both carefully designed with proper contrast.
- Typography: a clean modern sans font with excellent Cyrillic support (e.g. Inter / Segoe UI Variable / IBM Plex Sans — bundle it if licensing allows). Consistent type scale and 8-px spacing grid, generous whitespace, rounded corners, subtle shadows, smooth (not flashy) animations (≤200 ms).
- Layout suggestion (improve if you have a better idea): left sidebar with navigation (Location · BoM · Topology · IP plan · Power & Rack · Catalog · Projects · Settings), main content area, and a persistent **right/bottom summary bar** with live totals (switch count, firewall model, PoE/power load, estimated cost, warnings count). Input form on the left of the Location view with a live preview on the right.
- Inputs: steppers/spinners with keyboard entry, segmented controls for tier and mode, toggles with captions, chips for AP types, inline validation, sensible defaults, **undo/redo**, keyboard shortcuts (Ctrl+S, Ctrl+E export, Ctrl+Z/Y, Ctrl+N, Ctrl+K command palette), and tab-order that makes fast data entry possible without a mouse. Goal: a full location can be sized in **under 30 seconds**.
- BoM table: sortable, grouped by category, color-chip per category, expandable "reason" rows, quantity badges, tier-driven rows (HA/N+1/UPS/OOB) visually tagged so I can see *why* each exists.
- Empty states, loading states, error toasts, confirmation dialogs for destructive actions, helpful onboarding (first-run tour, 4–5 steps max, skippable).
- Responsive to window resizing and HiDPI; minimum window size sane; remember window geometry and last settings.
- Accessibility: contrast ≥ WCAG AA, scalable UI font size setting, full keyboard navigation.
- Performance: instant feel; heavy work (PDF/Excel export) off the UI thread with a progress indicator.

### 3.3 Engineering requirements
- **Strict separation**: `core/` (pure Python, no GUI imports: catalog, models, sizing engine, tiers, IP planner, power/PoE calc, validators, report data), `exporters/` (xlsx, pdf, png/svg, json/csv), `gui/` (all UI), `i18n/` (UA/EN strings), `data/` (default catalog, presets), `tests/`. The core must be usable from a CLI too (keep a thin CLI entry point that reproduces the old console flow, plus `--json` input mode for automation).
- Typed (type hints everywhere, dataclasses/pydantic models), clear docstrings, `ruff`/`black` formatting, `mypy`-friendly.
- **Tests (pytest)** for the sizing engine, tier rules, firewall ladder, threshold variant selection, IP subnet math/carving (including overlap and edge cases like 0 devices), PoE checks, reserve rounding, catalog validation, project save/load round-trip. Include golden-file tests that reproduce my prototype's outputs for a few reference scenarios (so I can confirm no regression), e.g. sockets=10, cameras=5, 1 AP group (FAP-241K × 1), tier 1, aggregation=y, reserve=n → 2× FS-1024E, 2× FG-120G, 1 Wi-Fi switch FS-124G-FPOE, 1 FS-148F, 1 FS-148F-FPOE, UPS ≈ 440 W line, OOB line, SLA reference row.
- No hardcoded business rules in GUI code. Everything configurable lives in the catalog/rules data.
- Robust error handling and logging to a rotating log file; never crash on bad input.
- Packaging: reproducible build script producing a single Windows `.exe` (and/or installer), app icon, version info; document in README. Pin dependencies (`requirements.txt` / `pyproject.toml`).
- Documentation: `README.md` (install, run, build, screenshots), `docs/RESEARCH.md`, `docs/ARCHITECTURE.md`, `docs/CATALOG.md` (how to edit catalog/rules), and an in-app Help page with the logic explained in plain language (the successor of my console manual).

---

## 4. How to work

1. Read `counter_location.py` and any whiteboard photos here. Summarize your understanding back to me in a few lines.
2. Do the research phase; write `docs/RESEARCH.md`. Ask me at most **one batched set of clarifying questions** (the "25х/15х" meaning, GUI framework choice if you need my input, price/branding preferences) — but do not wait for answers: proceed with clearly marked defaults.
3. Write a concise implementation plan (architecture, framework choice with reasons, milestones, design system: colors, type scale, spacing, components).
4. Build in milestones: (a) core engine + tests + catalog; (b) GUI shell + design system + live BoM; (c) diagram + IP plan + power/rack; (d) exporters (Excel/PDF/PNG); (e) projects, scenarios, presets, catalog editor; (f) polish, onboarding, packaging.
5. **Visually verify the GUI yourself**: launch it, take screenshots at different window sizes and in both themes, and iterate on spacing, alignment, contrast and consistency until it looks genuinely premium. Fix anything that looks default, cramped, or garish.
6. Run the full test suite and a manual end-to-end scenario before declaring done. Report what was verified and what remains uncertain (especially catalog values that could not be confirmed from datasheets).
7. Keep commits small and meaningful (initialize git if not already).

## 5. Definition of done
- I can launch the app, enter a location's numbers in well under a minute, see an accurate live BoM with understandable justifications, a clean diagram, optional IP plan, and export a customer-ready Excel + PDF.
- The look is cohesive, calm and professional in both light and dark themes.
- Catalog and rule values are verified against official Fortinet documentation (or clearly flagged where not), and editable without touching code.
- Tests pass, packaging produces a working `.exe`, docs are written.

Be ambitious and opinionated about design and UX; if you see a better approach than anything described here, do it and explain why.
