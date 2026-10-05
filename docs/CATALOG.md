# Editing the catalog and rules

The catalog is the single source of truth for models, limits, tiers and rule parameters. Change it in the
**Catalog** page (every edit is validated and applied immediately), or edit the JSON directly.

- Default (shipped): `sitesizer/data/catalog.json`. This is datasheet-verified and read-only in the app.
- Your copy: `%APPDATA%\SiteSizer\SiteSizer\catalog.json`, created on your first edit.
  **Catalog → Folder** opens it; **Reset to defaults** deletes it.
- **Import / Export** exchange catalogs with colleagues (for example a price list).

If a file is invalid, you get a list of the exact problems (`models.FS-148F.poe.budget_w: Input should be greater
than or equal to 0`, `categories.wifi_switch.base: unknown model 'FS-NOPE'`, …). The app keeps the last valid
catalog in the meantime.

## Structure

```jsonc
{
  "schema_version": 1,
  "meta": {"name": "...", "updated": "2026-10-04", "currency": "UAH"},
  "models": {
    "FS-124G-FPOE": {
      "kind": "switch",                    // switch | firewall | ap | accessory | license
      "family": "FortiSwitch 100G",
      "name": {"uk": "…", "en": "…"},      // description shown in the BoM
      "ports":   {"count": 24, "speed_gbps": 2.5},
      "uplinks": {"count": 6, "speed_gbps": 10, "type": "SFP+"},
      "poe": {"budget_w": 780, "budget_single_psu_w": null, "ports_at": 16, "ports_bt": 8},
      "psu": {"count": 1, "hot_swap": false, "redundant": false},
      "power_base_w": 100,                 // system power without PoE (used for UPS sizing)
      "power_max_w": 880,
      "rack_units": 1,
      "price": null,                       // unit price in meta.currency; null = unpriced
      "lifecycle": "active",               // active | eoo | eol  → EOO warning in checks
      "verified": "datasheet",             // datasheet | third_party | assumption
      "source": "https://…", "notes": "…"
    },
    "FG-120G": {
      "kind": "firewall",
      "firewall": {
        "max_switches": 32,
        "max_switches_fortios": [{"min_version": "7.6.1", "value": 48}],   // FortiOS-dependent limit
        "max_aps": 128, "max_aps_tunnel": 64, "ports_10g": 4,
        "throughput_gbps": {"firewall": 39, "ipsec": 35, "ips": 5.3, "ngfw": 3.1, "threat": 2.8, "ssl": 3},
        "form_factor": "rack", "sku_code": "F120G"
      }
    },
    "FAP-441K": {"kind": "ap", "ap": {"poe_class": "bt", "power_w": 41.7, "uplink_gbps": 10, "outdoor": false}}
  },
  "categories": {                          // which models serve each switch role
    "wifi_switch":   {"base": "FS-124G-FPOE", "premium": "FS-624F-FPOE", "endpoints_per_switch": 24},
    "access_switch": {"base": "FS-148F",      "premium": "FS-448E",      "endpoints_per_switch": 48},
    "camera_switch": {"base": "FS-148F-FPOE", "premium": "FS-448E-FPOE", "endpoints_per_switch": 48},
    "core_switch":   {"base": "FS-1024E",     "premium": "FS-1024E",     "endpoints_per_switch": 24}
  },
  "firewall_ladder": ["FG-80F", "FG-120G", "FG-200G", "FG-400G"],
  "ap_zones": {"high_density": {"label": {...}, "model": "FAP-441K", "clients_per_ap": 35}},
  "tiers": {"1": {"fw_ha": true, "core_redundant": true, "dual_psu": true, "ups": true, "oob": true,
                  "dual_uplinks": true, "dual_wan": true, "spare_percent": 10,
                  "forticare": "elite", "fortiguard": "enterprise", "sla": {...}}},
  "rules": { ... }                         // see below
}
```

## Rules

| Rule | Default | Meaning |
|---|---|---|
| `variant_mode` | `dual_psu` | How base/premium is chosen: `dual_psu` (confirmed 2-PSU choice), `quantity` (prototype: count ≥ threshold), `always_base`, `always_premium` |
| `variant_quantity_threshold` | 25 | Threshold for `quantity` mode |
| `bt_auto_upgrade` | true | Switch Wi-Fi to the premium model if the APs need more 802.3bt ports than the base model has |
| `aggregation_auto_threshold` | 3 | "Auto" core from this many access switches |
| `core_icl_links`, `core_port_check` | 2, true | MC-LAG ICL links; check the core's port capacity |
| `reserve_percent_default` | 20 | Growth reserve |
| `camera_watts_default` | 15 | PoE per camera (editable per location) |
| `wifi_clients_per_ap_default` | 15 | Wi-Fi subnet estimate when clients are unknown |
| `high_density_clients_per_ap` | 20 | Hint to use the high-density AP |
| `poe_warn_ratio`, `poe_autoscale` | 0.8, true | PoE warning level; add switches when the budget is exceeded |
| `oversubscription_warn` | 20 | Access → core oversubscription warning (:1) |
| `fw_throughput_headroom` | 0.3 | Headroom on threat-protection throughput |
| `power_model` | `datasheet` | `legacy` reproduces the prototype's 50/150/45 W estimate |
| `poe_psu_efficiency`, `ups_power_factor`, `ups_headroom` | 0.9, 0.9, 0.3 | UPS sizing |
| `rack_spare_ratio`, `patch_panel_ports`, `cable_manager_per_panel` | 0.3, 24, 0.5 | Rack and cabling |
| `copper_max_m`, `avg_cable_run_m_default` | 90, 40 | Cabling and IDF |
| `fortios_default` | 7.6.4 | Default FortiOS version for new locations |
| `ip.buffer`, `ip.smallest_prefix`, `ip.static_reserve`, `ip.segments[]` | 0.3, /30, 10 | IP planner and VLAN defaults |

## Passive section (`passive`)

Part numbers and quantities for structured cabling, the fibre backbone and rack power. Defaults are Corning
(see [RESEARCH.md §4a](RESEARCH.md)). Every referenced part should exist in `models` so the BoM gets a
description and price; scalar fields can be edited in the app (Catalog → Rules → *Passive*).

| Key | Default | Meaning |
|---|---|---|
| `jack`, `jack_pack` | KAXBSM-00104-C001-BP, 24 | Keystone jack SKU and pack size (2 jacks per link) |
| `panel`, `panel_ports` | MAXCSV-02408-C001, 24 | Patch panel (counted per switch) |
| `cable`, `cable_drum_m`, `cable_slack_m` | CCXEDB-DB047-C001-L7, 500, 3 | Cat.6A cable, drum length, slack per link |
| `cord_rack`, `cord_user` | 1 m / 2 m S/FTP cords | Rack + AP/camera side / work-area side |
| `outlet`, `outlet_ports` | UAXCSE-U0201-C001, 2 | Outlet box and jacks per box |
| `manager` | XE005315637 | 1U cable manager, one per switch block |
| `fiber_default` | auto | `auto` picks the cheapest type whose `max_10g_m` covers the longest run |
| `fiber.{om4,os2}` | see catalog | cable (12F), 12F/24F housings, LC-LC cord, Fortinet transceiver, 10G reach |
| `fiber_spare_ratio`, `fiber_slack_m` | 1.0, 20 | Spare fibres (+100 %) and slack per run |
| `splice_protector` | HSP-45S100-1 | One per fusion splice |
| `pdu`, `pdu_outlets` | PDU-8-C13, 8 | Rack PDU; A/B pairs for dual-PSU sites |

Cabinet sizes come from models with `rack_size_u` (default RACK-24U and RACK-42U).

## Common tasks

- **Add prices** — Catalog → Models → double-click *Price, UAH*. Price columns, totals, discount and VAT appear
  in the BoM, Excel and PDF as soon as any line has a price.
- **Add a new model** — select a similar row → *Duplicate model* → rename the SKU → adjust the specs. Then
  reference it in *Categories* or the *FortiGate ladder*.
- **Change what a tier does** — Catalog → Criticality tiers (for example, give tier 2 an HA firewall pair).
- **Go back to the prototype's behaviour** — Rules: `variant_mode = quantity`, `power_model = legacy`; or use
  `--compat` on the CLI.
