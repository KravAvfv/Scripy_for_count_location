"""Report data: turn a ``SiteResult`` into plain rows / dicts for exporters, CLI and clipboard."""

from __future__ import annotations

import csv
import io
from dataclasses import asdict
from datetime import date
from typing import Any

from ..i18n import Translator
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
    "reference",
)


def safe_filename(name: str, fallback: str = "Location") -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in ("-", "_", " ")).strip().replace(" ", "_")
    return cleaned or fallback


def default_export_name(site_name: str, ext: str, prefix: str = "BoM") -> str:
    return f"{prefix}_{safe_filename(site_name)}_{date.today().isoformat()}.{ext}"


def bom_columns(t: Translator, with_prices: bool) -> list[str]:
    cols = [t.t("col.category"), t.t("col.model"), t.t("col.qty"), t.t("col.reason")]
    if with_prices:
        cols += [t.t("col.unit_price"), t.t("col.total_price")]
    return cols


def bom_rows(result: SiteResult, with_prices: bool = False) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for line in result.bom:
        qty: Any = line.qty if line.qty is not None else "—"
        row: list[Any] = [line.category, line.model, qty, line.reason]
        if with_prices:
            row += [line.unit_price, line.total_price]
        rows.append(row)
    return rows


def ip_columns(t: Translator, with_addresses: bool) -> list[str]:
    cols = [t.t("col.segment"), t.t("col.vlan"), t.t("col.hosts"), t.t("col.prefix"), t.t("col.capacity")]
    if with_addresses:
        cols += [t.t("col.network"), t.t("col.gateway"), t.t("col.dhcp")]
    cols.append(t.t("col.note"))
    return cols


def ip_rows(result: SiteResult) -> list[list[Any]]:
    plan = result.ip_plan
    if plan is None:
        return []
    with_addr = bool(plan.base_network) and not plan.error
    rows = []
    for s in plan.segments:
        row: list[Any] = [s.name, s.vlan, s.hosts, f"/{s.prefix}", s.capacity]
        if with_addr:
            row += [s.network, s.gateway, s.dhcp_range]
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
