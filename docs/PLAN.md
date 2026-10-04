# Implementation plan

## Stack
Python 3.12+ · **PySide6** (Widgets, QSS, QGraphicsView, QPdfWriter, QSvgGenerator) · `openpyxl` ·
`pydantic` v2 (catalog/project schema + friendly validation errors) · `pytest` · `ruff` · `mypy` ·
PyInstaller (one-file .exe). Font: Inter (OFL), bundled. `pandas` / `matplotlib` are dropped.

## Layout
```
app/
  core/        pure Python, no Qt: models.py, catalog.py, sizing.py, tiers.py, ipplan.py,
               power.py, checks.py (warnings), pricing.py, project.py, presets.py, report.py
  exporters/   xlsx.py, pdf.py, diagram_render.py (QPainter: PNG/SVG/PDF), json_csv.py
  gui/         main_window.py, theme.py (tokens → QSS), widgets/ (stepper, segmented, toggle,
               card, chip, badge, toast, command palette), views/ (location, bom, topology,
               ipplan, power_rack, catalog, projects, compare, settings, help), undo.py
  i18n/        uk.json, en.json, tr() helper
  data/        catalog.json (default, validated), presets.json
  cli.py       console flow (prototype-compatible) + --json input/output
tests/         engine, tiers, ladder, variants, ipplan, poe, reserve, catalog, project round-trip,
               golden scenarios from the prototype
```

## Engine design
`SiteInput` (dataclass) → `size_site(input, catalog) -> SiteResult` (pure function).
`SiteResult` = counts per category, chosen models, `BomLine[]` (category, model, qty, reason,
tags: `tier|ha|n+1|addon|reference`, rule id), `Check[]` (severity, code, message), power/PoE/rack
summary, optional IP plan. Every rule reads its parameters from the catalog (`rules` section), so
the GUI never contains business logic. Reasons are i18n templates with parameters.

## Defaults that keep the prototype's outputs
* Variant mode `quantity` (threshold 25) — alternative `tier_psu` ready.
* Power model `datasheet` by default; `legacy` (50/150/45 W) kept for the golden test.
* FortiLink limits from datasheets (80F 24, 120G 48, 200G 64, 400G 96) — golden scenarios are small
  enough to be unaffected.

## Design system
* Colours (light): bg `#F5F7FA`, surface `#FFFFFF`, surface-alt `#EAF0F6`, border `#B7C4D1` /
  subtle `#DCE3EA`, text `#1F2D3A`, muted `#7C8894`, primary `#1F4E78` (hover `#24598A`,
  pressed `#1A4266`), success `#3E7D5A`, warning `#B07A2A`, error `#A8473F`, info `#3D6B94`
  (each with a 10 % tint background). Dark: bg `#12171D`, surface `#1A2129`, raised `#212A33`,
  border `#2E3944`, text `#E3E9EF`, muted `#8B98A5`, primary `#5B8FC7`.
* Type scale (Inter): 12 caption · 13 body · 15 subtitle · 18 title · 24 display; weights 400/500/600.
* 8-px grid (4 for fine tuning); radius 6 (inputs) / 10 (cards); shadow 0 1 3 rgba(0,0,0,.08).
* Motion: 150 ms ease-out for hover/expand/toasts only.
* Components: sidebar nav, cards, stepper spin box, segmented control, toggle with caption,
  chips, badges, severity callouts, BoM tree table, toasts, command palette, first-run tour.

## Milestones
a. core engine + catalog + tests (incl. golden) + CLI
b. GUI shell + theme + Location view + live BoM + summary bar + undo/redo
c. topology (live, pan/zoom, multi-site) + IP plan + power/rack
d. exporters: Excel, PDF, PNG/SVG, JSON/CSV, clipboard (threaded)
e. projects (multi-site), compare scenarios, presets, catalog editor, pricing
f. polish, onboarding, help page, i18n EN, screenshots in both themes, packaging, docs
