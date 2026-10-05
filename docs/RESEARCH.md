# Research notes — Fortinet site sizing tool

_Compiled 2026-10-04. All performance values are Fortinet "up to" figures. Every value below is
stored in `data/catalog.json` and is editable in the app. Items marked **⚠ DISCREPANCY** differ from the
whiteboard notes or from the console prototype (`counter_location.py`). Per the brief, the datasheet
value is used as the default and the difference is listed here instead of being silently changed._

Legend: ✅ confirmed in an official Fortinet datasheet / QSG / ordering guide · 🟡 third-party reseller
or catalog site only (verify before quoting to a customer) · ❓ not found.

---

## 1. Executive summary — what changes vs. the prototype

| # | Topic | Prototype / notes | Datasheet | Impact |
|---|-------|-------------------|-----------|--------|
| 1 | FG-80F FortiLink switch limit | 16 | **24** ✅ | 80F covers more sites |
| 2 | FG-120G FortiLink switch limit | 32 | **48 with FortiOS ≥ 7.6.1, 32 on older** ✅ | made a catalog field + "FortiOS ≥ 7.6.1" note |
| 3 | FG-400G FortiLink switch limit | 128 | **96** ✅ | ladder ceiling lower; > 96 → "FG-600G+" |
| 4 | Max FortiAPs per FortiGate | not checked | 80F 96 · 120G 128 · 200G 256 · 400G 512 (total; tunnel = half) ✅ | **new ladder criterion** |
| 5 | FS-124G-FPOE PoE | "24 AP ports" | 780 W, **only 8 of 24 ports are 802.3bt (90 W)**, 16 × af/at (30 W) ✅ | FAP-441K (41.7 W, needs bt) and FAP-241K (26.1 W, bt for full radios) can't all run at full power on one 124G-FPOE → new PoE-class check |
| 6 | FS-148F-FPOE PoE | 740 W | 740 W, 48 × af/at ✅ | matches |
| 7 | FS-448E-POE PoE | 740 W assumed for camera switches | **421 W** 🟡 (FS-448E-FPOE = 772 W) | premium camera switch has *less* PoE than the base one; with 15 W cameras only ~28 per switch fit the budget |
| 8 | FS-448E family lifecycle | in catalog | **End-of-Order 2026-09-13** 🟡 (two independent sources; it's also missing from the current FortiSwitch ordering guide ✅). Suggested successors: FS-348G / FS-348G-FPOE, or 600-series | flagged `lifecycle: "eoo"` in catalog, warning in UI |
| 9 | FS-1024E | "aggregation" | 24 × 10G SFP+ + 2 × 100G QSFP28, dual hot-swap PSU ✅, no PoE | **port limit**: one core switch can terminate ~20 access switches (24 – 2 ICL – 2 FortiLink); new check |
| 10 | FG-80F "1 Gbit, no SFP+" | – | Correct: 2 × GE RJ45/SFP shared WAN, 2 × GE RJ45 FortiLink, desktop form factor (rack tray optional) ✅ | access switches attach at 1 G (needs FN-TRAN-GC or copper on the switch side) |
| 11 | FG-120G "10 Gbit SFP+" | – | 4 × 10G SFP+ (FortiLink by default), 8 × GE SFP, 16 × GE RJ45 ✅ | ok |
| 12 | FG-200G "same as 120G, more switches" | – | 8 × 10G SFP+, 8 × 5G RJ45, 4 × GE SFP; 64 switches; threat-protection 6 Gbps (≈ 2× 120G) ✅ | also a throughput step, not only a switch-count step |
| 13 | FG-400G | – | 4 × 25G SFP28 + 4 × 10G SFP+ + 16 × GE SFP + 8 × 5G RJ45; 96 switches; TP 13 Gbps ✅ | ok |
| 14 | UPS estimate | fixed 50/150/45 W per box, **PoE not counted** | datasheet system power + real PoE load (AP max draw, camera class) | estimates go up a lot for PoE-heavy sites (e.g. 48 cameras × 15 W ≈ 720 W of PoE alone) |
| 15 | Third-party lifecycle data also lists FS-624F-FPOE (EOO 2024-09-12) and FS-1024E (EOO 2024-06-18) | – | **Both are still in Fortinet's current ordering guide** ✅. Probably an older hardware revision. | marked "verify on support.fortinet.com → Product Life Cycle" |

---

## 2. Equipment data

### 2.1 FortiSwitch

| Model | Access ports | Uplinks | PoE (std / budget) | PSU | Max power | RU | Source |
|---|---|---|---|---|---|---|---|
| **FS-124G-FPOE** | 24 × 2.5G/1G RJ45 | 6 × 10G SFP+ | 8 × bt 90 W + 16 × af/at 30 W, **780 W** | internal, single, no redundancy | 880 W (max with PoE) | 1 | ✅ Secure Access DS |
| **FS-624F-FPOE** | 24 × 5G/2.5G/1G RJ45 | 4 × 25G SFP28 | 24 × bt type 4 (90 W), **1440 W with dual PSU** | **2 × 1200 W dual hot-swap** (PoE load sharing); spare FS-PSU-1200 | 1680 W | 1 | ✅ Campus DS + OG |
| **FS-148F** | 48 × 1G RJ45 | 4 × 10G SFP+ | none | internal, single | 55.8 W avg / 57 W max | 1 | ✅ |
| **FS-148F-FPOE** | 48 × 1G RJ45 | 4 × 10G SFP+ | 48 × af/at, **740 W** | internal, single | 893.5 / 895.7 W | 1 | ✅ |
| **FS-448E** | 48 × 1G RJ45 | 4 × 10G SFP+ | none | dual redundant AC (QSG ✅) | 46.5 / 47.8 W 🟡 | 1 | QSG ✅ + 🟡 |
| **FS-448E-POE** | 48 × 1G RJ45 | 4 × 10G SFP+ | 48 × af/at, **421 W** 🟡 | dual redundant AC | 440 / 442 W 🟡 | 1 | 🟡 |
| FS-448E-FPOE (reference) | 48 × 1G RJ45 | 4 × 10G SFP+ | 48 × af/at, 772 W 🟡 | dual redundant AC | 921 / 924 W 🟡 | 1 | 🟡 |
| **FS-648F** | 32 × 2.5G + 16 × 5G RJ45 | 8 × 25G SFP28 | none | **2 × 350 W dual hot-swap** | 300 W | 1 | ✅ Campus DS |
| **FS-648F-FPOE** | 32 × 2.5G + 16 × 5G RJ45 | 8 × 25G SFP28 | 48 × bt type 4, **1800 W with 2 PSU (200–240 V), 780 W with 1 PSU** | **2 × 1200 W dual hot-swap** (PoE load sharing) | 2100 W | 1 | ✅ Campus DS |
| **FS-1024E** | 24 × 10G SFP+ | 2 × 100G QSFP28 | none | **dual hot-swap redundant** (spare FS-PSU-300) ✅ | 176 W 🟡 | 1 | QSG ✅ + OG ✅ |

Notes
* All FortiSwitches are managed by FortiGate via FortiLink **without a separate licence**. FortiCare
  support contracts are per switch (e.g. `FC-10-S1E24-247-02-DD` for FS-1024E).
* FS-1024E supports MCLAG (FortiLink MCLAG core pair) — Fortinet recommends ≥ 2 ICL links and fully
  meshed links between tiers (docs.fortinet.com, "Deploying MCLAG topologies").
* All PoE FortiSwitches are Alternative-A.

### 2.2 FortiAP (all Wi-Fi 7 except 234G)

| Model | Use (from notes) | Radios / MIMO | Uplink | PoE needed for full function | Max draw | Notes |
|---|---|---|---|---|---|---|
| **FAP-221K** | 3D farm | dual-radio 2×2 | 1 × 2.5G | 802.3at | 16.3 W | "retail grade" |
| **FAP-231K** | corridors | tri-radio 2×2 | 1 × 5G | 802.3at (af → 1×1) | 15.2 W | |
| **FAP-241K** | low density (< 20 clients/AP) | quad-radio 2×2 (incl. scan) | 1 × 10G | **802.3bt** (at → radio 3 limited to 15 dBm) | 26.1 W | |
| **FAP-441K** | high density (> 20 clients/AP) | quad-radio 4×4 | 2 × 10G | **802.3bt** or 2 × 802.3at | 41.7 W | 2nd port = hitless PoE failover |
| **FAP-234G** | outdoor | tri-radio 2×2, Wi-Fi 6E | 2.5G + 1G | 802.3at | 28 W | IP67, −40…+60 °C |

FortiGate-managed FortiAPs need **no per-AP licence** (only FortiCare per AP, and optional FortiGuard
AP services). Per-switch AP limit is therefore driven by **PoE class and budget**, not just port count.

### 2.3 FortiGate

| | FG-80F | FG-120G | FG-200G | FG-400G |
|---|---|---|---|---|
| Firewall (1518 B UDP) | 10 Gbps | 39 Gbps | 39 Gbps | 164 Gbps |
| IPsec VPN (512 B) | 6.5 Gbps | 35 Gbps | 36 Gbps | 55 Gbps |
| IPS | 1.4 Gbps | 5.3 Gbps | 9 Gbps | 25 Gbps |
| NGFW | 1 Gbps | 3.1 Gbps | 7 Gbps | 14 Gbps |
| **Threat protection** | 0.9 Gbps | 2.8 Gbps | 6 Gbps | 13 Gbps |
| SSL inspection | 0.715 Gbps | 3 Gbps | 7 Gbps | 11.5 Gbps |
| Concurrent sessions | 1.5 M | 3 M | 11 M | 28 M |
| **Max FortiSwitches** | **24** | **48** (FortiOS ≥ 7.6.1; 32 before) | **64** | **96** |
| **Max FortiAPs (total / tunnel)** | 96 / 48 | 128 / 64 | 256 / 128 | 512 / 256 |
| 10G-capable ports | none | 4 × 10G SFP+ | 8 × 10G SFP+ | 4 × 10G SFP+ + 4 × 25G SFP28 |
| Other ports | 2 × GE RJ45/SFP shared (WAN), 6 × GE + 2 × GE FortiLink | 16 × GE RJ45, 8 × GE SFP, MGMT, HA | 8 × GE, 8 × 5G RJ45, 4 × GE SFP, MGMT, HA | 8 × 5G RJ45, 16 × GE SFP, MGMT, HA |
| PSU | external DC adapter, 2nd adapter optional | dual non-swappable AC (1+1) | dual non-swappable AC (1+1) | dual non-swappable AC (1+1) |
| Power avg / max | 12.7 / 15.5 W | 38 / 40 W | 145 / 175 W | 230 / 283 W |
| Form factor | desktop (rack tray optional) | 1 RU | 1 RU | 1 RU |
| HA | A-A, A-P, clustering | same | same | same |

All ✅ from official Fortinet datasheets (fortinet.com/content/dam/fortinet/assets/data-sheets/…).
FG-80F also exists as `FG-80F-HA` SKUs bought in pairs (HA-entitled). Throughput values are used
for a **new optional check**: "WAN / inspected bandwidth" input vs. *threat-protection* throughput
with a configurable headroom (default 30 %).

---

## 3. The "25х" / "15х" markers — analysis

The prototype treats them as **quantity thresholds** (≥ 25 switches → premium model). That is hard to
justify technically: nothing in the datasheets changes at 25 or 15 units, and a site with ≥ 25 Wi-Fi
switches is rare. Comparing what the premium/base pairs actually differ in:

| Pair | Marker | Premium model | Base model |
|---|---|---|---|
| FS-624F-FPOE / FS-124G-FPOE | 25х / 15х | **2 × hot-swap PSU**, 24 × bt, 1440 W, 25G uplinks | **1 PSU**, 8 × bt, 780 W, 10G uplinks |
| FS-448E / FS-148F | 25х / 15х | **dual redundant PSU** | **single PSU** |
| FS-448E-POE / FS-148F-FPOE | 25х / 15х | **dual redundant PSU** | **single PSU** |

**Every "25х" model has two power supplies and every "15х" model has one.** The most likely reading
is therefore "2 БЖ" / "1 БЖ" (2 power supplies / 1 power supply) — or "2× PSU" / "1× PSU" — rather
than a quantity. This also fits the word "hot-swap" written next to the first marker.

Other readings considered: a port count (no — all are 24/48 ports), uplink speed "25G" vs "15G"
(624F has SFP28 25G uplinks, but 448E doesn't), price ratio (possible, but unverifiable).

**Implementation:** the variant rule is data-driven, with three modes per category, selectable in
the catalog/UI:
* `quantity` — prototype behaviour (premium when count ≥ threshold, default 25). **Current default**,
  so the prototype's outputs are reproduced exactly.
* `tier_psu` — premium (dual-PSU) model when the criticality tier requires redundant PSUs
  (default: tiers 1–2). **Recommended** if the "PSU" reading is right.
* `always_base` / `always_premium`.

**Decision (confirmed by the engineer):** the markers mean 2 / 1 power supplies. The default rule is now
`dual_psu`: tiers 1–2 *recommend* the dual-PSU model, and the app asks the engineer to confirm (the
`redundant_psu` input is `None` until confirmed). The quantity rule is kept as `variant_mode: quantity` and is
used by the golden tests.

## 3a. Other decisions after the clarification round (2026-10-04)

| Topic | Decision |
|---|---|
| FS-448E / FS-448E-POE (End-of-Order per third-party sites) | **Kept** FS-448E as the premium (dual-PSU) access model. For cameras the engineer chose **FS-448E-FPOE** (772 W PoE, dual redundant PSU, 🟡 third-party data) instead of the whiteboard FS-448E-POE (421 W). EOO status is an info note. FS-448E-POE and FS-648F / FS-648F-FPOE remain as catalog alternatives. |
| 802.3bt shortage on FS-124G-FPOE | **Auto-upgrade** to FS-624F-FPOE (rule `bt_auto_upgrade`), explained in the BoM line and tagged "auto". |
| Currency | UAH, optional VAT and discount; prices empty by default (price columns hidden). |
| FortiOS version | Per-location input (default 7.6.4); FG-120G's 32/48 limit follows it. |
| PoE redundancy caveat (new finding) | FS-624F-FPOE (and the alternative FS-648F-FPOE) have only **780 W PoE with one working PSU**. When the load per switch exceeds that, an info check warns that a PSU failure would drop some devices. |

---

## 4. Senior-engineer best practices adopted as rules / add-ons

| Area | Practice | How the tool uses it |
|---|---|---|
| Firewall HA | FGCP active-passive pair for critical sites; same model, same firmware, dedicated HA heartbeat links (2). HA-entitled SKUs exist for small models. | Tier 1: FW × 2, + 2 heartbeat patch cords |
| Core redundancy | FortiLink MCLAG core pair (FS-1024E × 2), ≥ 2 ICL links, access switches dual-homed. | Tier 1: core × 2; core port check: `24 − ICL − FortiLink uplinks ≥ access switches × uplinks/switch`; adds core pairs if exceeded |
| Uplink oversubscription | Industry guideline: access → distribution ≤ 20:1, distribution → core ≤ 4:1 (Cisco campus design guide). | Computed per category (downlink Gbps ÷ uplink Gbps); warning > 20:1 |
| PoE headroom | Keep ≥ 20 % budget headroom; PSE per-port power: af 15.4 W, at 30 W, bt type 3 60 W, bt type 4 90 W. | AP draw from datasheet; camera class selectable (default 12.95 W class 3 / editable); warns at > 80 % of budget and when bt ports are insufficient |
| Cabling | TIA-568 copper channel 100 m (90 m permanent link + 10 m cords). Beyond ~90 m → additional IDF/telecom room with fibre uplink. | "max cable run" input → IDF count estimate + fibre uplinks, transceivers |
| Rack & power | 1 RU per switch/FW, patch panel per 24/48 ports, cable managers, ≥ 20–30 % spare RU; UPS sized on real load with ≥ 30 % headroom, PF 0.9 | Rack planner: 24U/42U cabinets with a drawn front elevation, PDUs, UPS VA (see §4a) |
| WAN | Dual WAN / SD-WAN, LTE backup (FortiExtender) for critical sites | Tier-driven add-on lines |
| OOB | Independent 4G/5G management path to FW/console | Tier 1 line (as prototype) |
| Segmentation | Separate VLANs: Data, Voice, Wi-Fi corporate, Guest, CCTV, IoT, Management (native VLAN unused). | IP planner carves all enabled segments from a base prefix |
| DHCP sizing | Size for peak concurrent devices + 30 % growth; leases short for guest (1–4 h). | Prototype formula `ceil(h × 1.3) + 2` kept; gateway = first usable; DHCP pool = rest minus reserved block for static |
| Licensing | FortiGuard bundles: **ATP** (IPS, AV, sandbox, app control) ⊂ **UTP** (+ web/DNS filter, anti-botnet) ⊂ **Enterprise** (+ CASB, DLP, IoT, ASM, inline AI malware). FortiCare Premium (24×7) included in bundles; FortiCare Elite = 15-min response. | Tier → recommended bundle; line items per FortiGate; FortiCare per switch/AP |
| Management | FortiManager / FortiAnalyzer (or cloud) for multi-site; FAZ sized by log rate (GB/day). | Project-level note when > 1 site |
| Spares | Tier 1: on-site spare (1 per model type, or 5–10 %), Tier 2: cold spare in warehouse. | Spare % per tier in catalog |
| Documentation | As-built diagram, IP plan, port map, config backups, acceptance test | Exported in PDF report "Deliverables" section |

---

## 4a. Passive infrastructure (Corning) — added 2026-10-05

Vendor priority: **Fortinet** for active equipment and transceivers, **Corning** for cabling. Part numbers were
checked in the Corning EMEA eCatalog and at the Ukrainian Corning distributor CMS (cms.ua), which stocks the
Everon copper line and LANscape fibre housings.

| Item | Part number | Why this one | Verified |
|---|---|---|---|
| Cat.6A jack | **KAXBSM-00104-C001-BP** — Everon KS500S, shielded Keystone, pack of 24 (B6 = pack of 6) | Tool-less, 10GBase-T, 4PPoE (802.3bt) for FAP-241K/441K | eCatalog ✅ |
| Patch panel | **MAXCSV-02408-C001** — Everon 19" 1U, 24 Keystone/VOL ports, unloaded, black | Takes the KS500S jacks; sold in Ukraine | eCatalog ✅ |
| Cable | **CCXEDB-DB047-C001-L7** — Everon S/FTP 550/23 Cat.6A, LSZH, CPR B2ca | PIMF shielded, 10G + PoE++; sold per metre (counted in 500 m drums) | cms.ua 🟡 |
| Patch cords | **CCAAGB-G5002-A010-C0 / -A020-C0** — Everon Class EA S/FTP LSZH 1 m / 2 m | Shielded system end-to-end. A030/A060/A150 confirmed in eCatalog; A010/A020 follow the same scheme | assumption 🟡 |
| Outlet | **UAXCSE-U0201-C001** — Everon surface box for 2 Keystone/VOL jacks | 2 sockets per box; APs/cameras get one box each | eCatalog ✅ |
| Cable manager | **XE005315637** — Everon 19" 1U | One per "patch panels + switch" block | cms.ua 🟡 |
| Fibre cable OM4 | **012TEU-83198A2G** — FREEDM gel-filled central tube, dielectric armour, U-DQ(ZN)BH, 12× OM4, LSZH, Ø 5.6 mm | Indoor/outdoor backbone, 10G SR up to ~400 m | eCatalog ✅ |
| Fibre cable OS2 | **012EEU-13122A2G** — 12× OS2 indoor/outdoor | Runs > 400 m (10G LR, 10 km) | reseller 🟡 |
| Fibre housing | **LAN1-12AD/24AD-PGTL-B** (OM3/OM4), **LAN1-12AE/24AE-PGTL-B** (OS2) — LANscape 1U, shuttered LC duplex, pigtails + splice pack | All-in-one: no separate pigtails/cassettes to count | cms.ua 🟡 |
| Splice protector | **HSP-45S100-1** (S46998-A4-A29), 45 mm | One per fusion splice | cms.ua 🟡 |
| Fibre patch cords | **050502Q5120002M** (LC-LC OM4 2 m), **040402G5120002M** (LC-LC OS2 2 m) | US part numbers — ask the distributor for the LSZH EU variant | reseller 🟡 |
| Transceivers | **FN-TRAN-SFP+SR** (OM4) / **FN-TRAN-SFP+LR** (OS2), **FN-CABLE-SFP+3** DAC inside a rack | Fortinet-coded optics for FortiLink | datasheet ✅ |
| Racks, PDU | **RACK-24U**, **RACK-42U** (generic; e.g. CMS MGSE 24U/42U), **PDU-8-C13** 1U 8× C13 | Corning does not make cabinets; sizes limited to 24U and 42U as used in the company | assumption |

Calculation rules (`sitesizer/core/passive.py`):

* **Copper** — one permanent link per socket, camera and AP; cable = links × (average run + 3 m slack), rounded up to
  500 m drums; 2 jacks per link (panel + outlet); patch panels counted **per switch** (a 48-port switch with
  48 endpoints gets 2 panels), one cable manager per switch block; outlets: sockets ÷ 2 + one per AP/camera;
  1 m cords for every panel port and at every AP/camera, 2 m cords at every socket.
* **Closets** — `ceil(max run / 90 m)` closets; switches are spread evenly, closet 0 is the MDF with the FortiGate,
  core, OOB and UPS. The fibre run to IDF *k* is estimated as `k × max run / closets` unless entered.
* **Fibre** — per IDF: uplinks × 2 fibres × (1 + 100 % spare) → 12-fibre cables; a LANscape housing at both
  ends (24F where two cables fit), splice protectors per spliced fibre, 2 LC-LC cords and 2 transceivers per link.
  Auto type: OM4 if the longest run + slack ≤ 400 m, otherwise OS2.
* **Cabinets** — 24U if everything + PDUs fits with 30 % spare, otherwise 42U (or the size chosen by the user);
  more cabinets are added and filled evenly when needed. Layout top→bottom: fibre housings, OOB, FortiGate,
  core, then for each switch its patch panels → cable manager → switch; UPS at the bottom with PDUs above it.
  Dual-PSU sites get separate A/B PDUs.

---

## 5. UX research — comparable tools

* **Fortinet FortiConverter / Fortinet product selector, Cisco Meraki sizing, Ubiquiti Design Center**:
  short input form, live result, model cards with "why". Ubiquiti's floor-plan tool is overkill here,
  but its *right-hand live BoM* is the pattern we copy.
* **Linear / Figma / Notion**: calm neutral surfaces, one accent colour, keyboard-first (Ctrl+K
  palette), inline explanations instead of modal help.
* **draw.io / NetBox topology views**: hierarchical layered layout, pan/zoom, PNG/SVG export.
* Take-aways: (1) inputs left, live results right, totals always visible; (2) every derived line
  shows its reason; (3) presets for 80 % of cases; (4) a diff view for "what if" scenarios.

---

## 6. GUI framework decision

| Option | Look | Speed | Packaging (1 × .exe) | HiDPI / Cyrillic | Charting / diagram | Verdict |
|---|---|---|---|---|---|---|
| **PySide6 (Qt Widgets + QSS + custom painting)** | Fully custom, can look like Fluent/Linear with effort | Native, instant | PyInstaller one-file ≈ 60–90 MB, LGPL | Excellent (per-monitor DPI, HarfBuzz shaping) | QGraphicsView (pan/zoom), QPainter → PNG/SVG/PDF from the *same* renderer; QPdfWriter for reports | **Chosen** |
| Flet (Flutter) | Material look out of the box | Good | 100+ MB, Flutter engine | Good | Weaker for custom vector diagrams / PDF | – |
| CustomTkinter | Modern-ish, limited components | Okay | Small | Tk text rendering is weaker; no real table view | Poor | – |
| NiceGUI / pywebview / Tauri+Python | Best CSS freedom | Good | Two runtimes, more moving parts, WebView2 dependency | Good | Good (SVG) | – |

**Why PySide6:** a single native process (no web server, no WebView2), very good HiDPI and Cyrillic
rendering, a real table/model framework for the BoM and catalog editor, one painter-based diagram
renderer reused for screen, PNG, SVG and PDF (so the export always matches what you see), PDF
generation without extra dependencies (QPdfWriter + bundled font), and dependable PyInstaller support.
The "default grey Qt" look is avoided with a token-based QSS theme generated from Python (light/dark),
custom-painted segmented controls, toggles, cards and chips. Font: **Inter** (SIL OFL 1.1 — bundling
allowed), with Segoe UI Variable as fallback.

Exports: Excel via `openpyxl` (already used by the prototype); PDF and diagram via Qt. `pandas` and
`matplotlib` are no longer needed, which shrinks the .exe considerably.

---

## 7. Sources

Official Fortinet:
* FortiSwitch Secure Access (100F/100G) data sheet — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/pdf/fortiswitch-secure-access-series.pdf
* FortiSwitch Campus (300/400/600 series) data sheet — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/fortiswitch-campus-series.pdf
* FortiSwitch ordering guide — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/og-fortiswitch.pdf
* FortiSwitch 448E Series QuickStart Guide — https://fortinetweb.s3.amazonaws.com/docs.fortinet.com/v2/attachments/9f94e4e9-920c-11ea-aafb-00505692583a/FortiSwitch-448E-Series-QSG.pdf
* FortiSwitch 1024E Series QuickStart Guide — https://fortinetweb.s3.amazonaws.com/docs.fortinet.com/v2/attachments/002ca2c6-586f-11ec-bdf2-fa163e15d75b/FortiSwitch-1024E-QSG.pdf
* FortiAP Series data sheet — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/pdf/fortiap-series.pdf
* FortiGate/FortiWiFi 80F data sheet — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/fortigate-fortiwifi-80f-series.pdf
* FortiGate 120G data sheet — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/fortigate-120g-series.pdf
* FortiGate 200G data sheet — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/fortigate-200g-series.pdf
* FortiGate 400G data sheet — https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/pdf/fortigate-400g-series.pdf
* FortiGuard security bundles — https://www.fortinet.com/support/support-services/fortiguard-security-subscriptions/fortigate-security-bundles
* FortiLink MCLAG topologies — https://docs.fortinet.com/document/fortiswitch/7.6.4/fortilink-guide/801194/deploying-mclag-topologies

Corning (passive):
* Everon KS500S jack — https://ecatalog.corning.com/optical-communications/EMEA/en_GB/Copper-Hardware/Copper-Jacks/Copper-Jacks-Shielded/Everon%C2%AE-Copper-Datacom-KS500S-Shielded-Jack/p/everon-copper-datacom-ks500s-shielded-jack
* Everon patch panel MAXCSV-02408-C001 — https://ecatalog.corning.com/optical-communications/EMEA/en/Copper-Hardware/Everon%C2%AE-Copper-Datacom-Patch-Panels/Everon%C2%AE-Copper-Datacom-VOL-Patch-panel-19-inch/p/MAXCSV-02408-C001
* Everon S/FTP patch cords — https://ecatalog.corning.com/optical-communications/EMEA/en/Products/Copper-Cable-Assemblies/Everon%C2%AE-Copper-Datacom-Class-EA-Patch-Cord,-2xRJ45,-Cat-6A,-S-FTP-LSZH-AWG-26/p/CCAAGB-G5002-A030-C0
* Everon outlet UAXCSE-U0201-C001 — https://ecatalog.corning.com/optical-communications/IN/en/Copper-Hardware/Copper-Outlets/Everon%C2%AE-Copper-Datacom-Terminal-Outlet-VOL-and-Keystone/p/UAXCSE-U0201-C001
* FREEDM U-DQ(ZN)BH 012TEU-83198A2G — https://ecatalog.corning.com/optical-communications/EMEA/en/Fiber-Optic-Cables/Indoor-Outdoor/Indoor-Outdoor-Duct-Cables/FREEDM%C2%AE-Gel-filled-Central-Tube-Dielectric-Armor-Indoor-Outdoor-Cable%2C-U-DQ%28ZN%29BH/p/012TEU-83198A2G
* CMS (Ukrainian distributor): cable — https://cms.ua/en/catalogue/copper_cable/lan_cable/ccxedb-db047-c001-l7/ ,
  LANscape housings — https://cms.ua/en/catalogue/fiber_optic_system/lanscape_fiber_housing/ ,
  organizers — https://cms.ua/en/catalogue/racks/organizers/ , cabinets — https://cms.ua/en/catalogue/racks/flour_racks/

Third-party (🟡):
* FS-448E lifecycle / replacements — https://fortineteol.com/hardware/fortiswitch , https://datacenter360.ca/faqs/fortinet-faqs/end-of-life-product-life-cycle/
* FS-448E-POE/FPOE specs — https://datacenter360.ca/fortinet/fortiswitch/fortiswitch-448e-poe-switch/ , https://netboxlabs.com/ndx/fortinet/fortinet-fs-448e/
* FS-1024E power — https://datacenter360.ca/fortinet/fortiswitch/fortiswitch-1024e-switch/
* Oversubscription guideline — https://www.cisco.com/c/dam/global/shared/assets/pdf/cisco_enterprise_campus_infrastructure_design_guide.pdf
* GUI framework comparison — https://www.pythonguis.com/faq/which-python-gui-library/

**Still unverified:** FS-448E family power figures and EOO date (no official PDF found — check
support.fortinet.com → Product Life Cycle), FS-1024E max power (176 W), FS-624F-FPOE / FS-1024E
third-party EOO entries.
