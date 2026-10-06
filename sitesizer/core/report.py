"""Report data: turn a ``SiteResult`` into plain rows / dicts for exporters, CLI and clipboard."""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from ..i18n import Translator
from .catalog import SECTION_ORDER, Catalog, section_of
from .models import SiteResult

BOM_GROUP_ORDER = (
    "ap",
    "wifi_switch",
    "access_switch",
    "camera_switch",
    "core_switch",
    "firewall",
    "power",
    "transceiver",
    "cabling",
    "fiber",
    "rack",
    "license",
    "spare",
    "custom",
    "reference",
)


@dataclass
class SpecRow:
    """One row of the Excel specification template (columns A–H)."""

    code: str
    name: str
    unit: str
    qty: int
    price_min: float | None
    price: float | None
    model: str = ""


def display_name(model: str, name: str) -> str:
    """Template-style item name: keep imported names as they are, otherwise ``model — description``."""
    if not name:
        return model
    if not model or model.lower() in name.lower():
        return name
    return f"{model} — {name}"


def spec_sections(
    result: SiteResult, catalog: Catalog, t: Translator, only_used: bool = False
) -> list[tuple[str, list[SpecRow]]]:
    """Rows of the specification grouped by template section (``sks``, ``network``, ``works``).

    Every catalog item is listed in template order with its quantity (0 when unused), like the
    company's Excel template; lines that are not in the catalog and hand-added lines are appended
    to their section. ``only_used`` drops the zero rows.
    """
    qty: dict[str, int] = {}
    price: dict[str, tuple[float | None, float | None]] = {}
    extras: dict[str, list[SpecRow]] = {k: [] for k in SECTION_ORDER}
    for line in result.bom:
        if not line.qty and line.qty != 0:
            continue
        if line.group == "custom" or line.model not in catalog.models:
            dev = catalog.models.get(line.model)
            section = (
                next((c.section for c in result.input.bom.custom if f"custom:{c.id}" == line.key), "network")
                if line.group == "custom"
                else (section_of(line.model, dev) if dev else "network")
            )
            if line.qty:
                extras[section].append(
                    SpecRow(
                        line.code,
                        display_name(line.model if dev else "", line.description or line.model),
                        line.unit,
                        line.qty,
                        line.price_min,
                        line.unit_price,
                        line.model,
                    )
                )
            continue
        qty[line.model] = qty.get(line.model, 0) + line.qty
        if line.manual or line.model not in price:
            price[line.model] = (line.price_min, line.unit_price)
    sections: dict[str, list[SpecRow]] = {k: [] for k in SECTION_ORDER}
    listed: set[str] = set()
    for model, dev in catalog.template_models():
        n = qty.get(model, 0)
        if only_used and not n:
            continue
        pmin, pmain = price.get(model, (dev.price_min, dev.price))
        sections[section_of(model, dev)].append(
            SpecRow(dev.code, dev.spec_name or display_name(model, t.pick(dev.name)), dev.unit, n, pmin, pmain, model)
        )
        listed.add(model)
    for model, n in qty.items():
        if model in listed or not n:
            continue
        dev = catalog.models[model]
        pmin, pmain = price.get(model, (dev.price_min, dev.price))
        sections[section_of(model, dev)].append(
            SpecRow(dev.code, dev.spec_name or display_name(model, t.pick(dev.name)), dev.unit, n, pmin, pmain, model)
        )
    for key in SECTION_ORDER:
        sections[key].extend(extras[key])
    return [(key, rows) for key, rows in sections.items() if rows]


def safe_filename(name: str, fallback: str = "Location") -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in ("-", "_", " ")).strip().replace(" ", "_")
    return cleaned or fallback


def default_export_name(site_name: str, ext: str, prefix: str = "BoM") -> str:
    return f"{prefix}_{safe_filename(site_name)}_{date.today().isoformat()}.{ext}"


def bom_columns(t: Translator, with_prices: bool) -> list[str]:
    cols = [
        t.t("col.category"),
        t.t("col.model"),
        t.t("col.code"),
        t.t("col.description"),
        t.t("col.unit"),
        t.t("col.qty"),
    ]
    if with_prices:
        cols += [t.t("col.unit_price"), t.t("col.total_price")]
    return cols


def bom_rows(result: SiteResult, with_prices: bool = False) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for line in result.bom:
        qty: Any = line.qty if line.qty is not None else "—"
        row: list[Any] = [line.category, line.model, line.code, line.description, line.unit, qty]
        if with_prices:
            row += [line.unit_price, line.total_price]
        rows.append(row)
    return rows


def ip_columns(t: Translator, with_addresses: bool) -> list[str]:
    cols = [t.t("col.vlan"), t.t("col.segment"), t.t("col.hosts"), t.t("col.prefix"), t.t("col.capacity")]
    if with_addresses:
        cols += [t.t("col.network"), t.t("col.mask"), t.t("col.gateway"), t.t("col.dhcp")]
    cols.append(t.t("col.note"))
    return cols


def ip_rows(result: SiteResult) -> list[list[Any]]:
    plan = result.ip_plan
    if plan is None:
        return []
    with_addr = plan.has_addresses
    rows = []
    for s in plan.segments:
        row: list[Any] = [s.vlan, s.name, s.hosts, f"/{s.prefix}", s.capacity]
        if with_addr:
            row += [s.network, s.mask, s.gateway, s.dhcp_range]
        row.append(s.note)
        rows.append(row)
    return rows


def bom_tsv(result: SiteResult, t: Translator, with_prices: bool = False) -> str:
    """Tab-separated BoM for the clipboard (pastes cleanly into Excel / Word)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow(bom_columns(t, with_prices))
    for row in bom_rows(result, with_prices):
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


def bom_csv(result: SiteResult, t: Translator, with_prices: bool = False) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(bom_columns(t, with_prices))
    for row in bom_rows(result, with_prices):
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


def result_to_dict(result: SiteResult) -> dict[str, Any]:
    """JSON-friendly snapshot of a result (for ``--json`` automation and JSON export)."""
    return {
        "input": result.input.model_dump(mode="json"),
        "tier": result.tier_id,
        "dual_psu": result.dual_psu,
        "dual_psu_confirmed": result.dual_psu_confirmed,
        "fortios_version": result.fortios_version,
        "counts": {
            "sockets": result.counts.sockets,
            "cameras": result.counts.cameras,
            "aps": result.counts.aps,
            "wifi_clients": result.counts.wifi_clients,
        },
        "switches": {
            key: {
                "model": c.model,
                "count": c.count,
                "poe_load_w": round(c.poe_load_w, 1),
                "oversubscription": round(c.oversubscription, 2),
            }
            for key, c in {**result.categories, "core_switch": result.core}.items()
            if c.count
        },
        "firewall": (
            {"model": result.firewall.model, "count": result.firewall.count, "fits": result.firewall.fits}
            if result.firewall
            else None
        ),
        "bom": [
            {
                "group": line.group,
                "category": line.category,
                "model": line.model,
                "qty": line.qty,
                "reason": line.reason,
                "details": line.details,
                "tags": line.tags,
                "unit_price": line.unit_price,
                "total_price": line.total_price,
            }
            for line in result.bom
        ],
        "checks": [
            {"severity": c.severity.value, "code": c.code, "message": c.message, "hint": c.hint} for c in result.checks
        ],
        "power": {k: v for k, v in asdict(result.power).items() if k != "breakdown"},
        "rack": asdict(result.rack),
        "ip_plan": asdict(result.ip_plan) if result.ip_plan else None,
    }
