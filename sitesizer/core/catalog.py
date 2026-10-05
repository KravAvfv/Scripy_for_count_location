"""Equipment catalog and rule set: schema, loading, validation.

The catalog is a human-editable JSON file (``sitesizer/data/catalog.json`` ships as the default).
Everything the sizing engine needs — models, category mapping, firewall ladder, tiers and rule
parameters — comes from here, so business rules are never hard-coded in the GUI.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

log = logging.getLogger(__name__)

LText = dict[str, str]
"""Localized text: ``{"uk": "...", "en": "..."}``."""

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.json"

CATEGORY_KEYS = ("wifi_switch", "access_switch", "camera_switch", "core_switch")
EDGE_CATEGORIES = ("wifi_switch", "access_switch", "camera_switch")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PortSpec(_Strict):
    count: int = Field(ge=0)
    speed_gbps: float = Field(gt=0)
    type: str = "RJ45"


class PoESpec(_Strict):
    budget_w: float = Field(ge=0)
    budget_single_psu_w: float | None = Field(default=None, ge=0)
    ports_at: int = Field(default=0, ge=0)
    ports_bt: int = Field(default=0, ge=0)

    @property
    def ports_total(self) -> int:
        return self.ports_at + self.ports_bt


class PsuSpec(_Strict):
    count: int = Field(default=1, ge=1)
    hot_swap: bool = False
    redundant: bool = False


class FortiOSLimit(_Strict):
    min_version: str
    value: int = Field(ge=0)


class Throughput(_Strict):
    firewall: float = Field(ge=0)
    ipsec: float = Field(ge=0)
    ips: float = Field(ge=0)
    ngfw: float = Field(ge=0)
    threat: float = Field(ge=0)
    ssl: float = Field(ge=0)


class FirewallSpec(_Strict):
    max_switches: int = Field(ge=0)
    max_switches_fortios: list[FortiOSLimit] = Field(default_factory=list)
    max_aps: int = Field(ge=0)
    max_aps_tunnel: int = Field(default=0, ge=0)
    ports_10g: int = Field(default=0, ge=0)
    throughput_gbps: Throughput
    sessions_m: float = Field(default=0, ge=0)
    form_factor: Literal["desktop", "rack"] = "rack"
    sku_code: str = ""

    def switch_limit(self, fortios_version: str | None) -> int:
        """FortiLink switch limit for a given FortiOS version (highest matching override)."""
        limit = self.max_switches
        if fortios_version:
            current = parse_version(fortios_version)
            for override in sorted(self.max_switches_fortios, key=lambda o: parse_version(o.min_version)):
                if current >= parse_version(override.min_version):
                    limit = override.value
        return limit


class ApSpec(_Strict):
    poe_class: Literal["af", "at", "bt"] = "at"
    power_w: float = Field(ge=0)
    uplink_gbps: float = Field(default=1, gt=0)
    outdoor: bool = False
    wifi: str = ""
    radios: int = Field(default=2, ge=1)
    mimo: str = ""


Kind = Literal["switch", "firewall", "ap", "accessory", "license"]
Verified = Literal["datasheet", "third_party", "assumption"]
Lifecycle = Literal["active", "eoo", "eol"]


class Device(_Strict):
    """One orderable item in the catalog."""

    kind: Kind
    family: str = ""
    name: LText = Field(default_factory=dict)
    ports: PortSpec | None = None
    uplinks: PortSpec | None = None
    poe: PoESpec | None = None
    psu: PsuSpec | None = None
    firewall: FirewallSpec | None = None
    ap: ApSpec | None = None
    power_base_w: float | None = Field(default=None, ge=0)
    power_max_w: float | None = Field(default=None, ge=0)
    rack_units: int = Field(default=0, ge=0)
    rack_size_u: int | None = Field(default=None, ge=1)
    ups_va: int | None = Field(default=None, ge=1)
    price: float | None = Field(default=None, ge=0)
    lifecycle: Lifecycle = "active"
    verified: Verified = "assumption"
    source: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _check_kind_sections(self) -> Device:
        if self.kind == "switch" and (self.ports is None or self.uplinks is None):
            raise ValueError("switch needs 'ports' and 'uplinks'")
        if self.kind == "firewall" and self.firewall is None:
            raise ValueError("firewall needs a 'firewall' section")
        if self.kind == "ap" and self.ap is None:
            raise ValueError("access point needs an 'ap' section")
        return self

    @property
    def has_poe(self) -> bool:
        return self.poe is not None and self.poe.budget_w > 0

    @property
    def dual_psu(self) -> bool:
        return self.psu is not None and self.psu.count >= 2 and self.psu.redundant


class Category(_Strict):
    label: LText
    short: LText = Field(default_factory=dict)
    base: str
    premium: str
    endpoints_per_switch: int = Field(gt=0)


class ApZone(_Strict):
    label: LText
    model: str
    clients_per_ap: int = Field(default=15, ge=0)


class Tier(_Strict):
    label: LText
    description: LText = Field(default_factory=dict)
    sla: LText = Field(default_factory=dict)
    fw_ha: bool = False
    core_redundant: bool = False
    dual_psu: bool = False
    ups: bool = False
    oob: bool = False
    dual_uplinks: bool = False
    dual_wan: bool = False
    spare_percent: float = Field(default=0, ge=0, le=100)
    forticare: Literal["none", "premium", "elite"] = "premium"
    fortiguard: Literal["none", "atp", "utp", "enterprise"] = "utp"


class IpSegment(_Strict):
    id: str
    vlan: int = Field(ge=1, le=4094)
    label: LText
    source: Literal["sockets", "voice", "wifi", "guest", "cameras", "iot", "mgmt"]
    dhcp: bool = True
    default: bool = True


class IpRules(_Strict):
    buffer: float = Field(default=0.3, ge=0, le=10)
    smallest_prefix: int = Field(default=30, ge=8, le=30)
    static_reserve: int = Field(default=10, ge=0)
    segments: list[IpSegment]


class LegacyPower(_Strict):
    switch_base: float = 50
    switch_poe: float = 150
    firewall: float = 45


class Rules(_Strict):
    variant_mode: Literal["dual_psu", "quantity", "always_base", "always_premium"] = "dual_psu"
    variant_quantity_threshold: int = Field(default=25, ge=0)
    bt_auto_upgrade: bool = True
    aggregation_auto_threshold: int = Field(default=3, ge=0)
    core_icl_links: int = Field(default=2, ge=0)
    core_port_check: bool = True
    reserve_percent_default: float = Field(default=20, ge=0, le=500)
    camera_watts_default: float = Field(default=15, ge=0, le=90)
    wifi_clients_per_ap_default: int = Field(default=15, ge=0)
    high_density_clients_per_ap: int = Field(default=20, ge=1)
    poe_warn_ratio: float = Field(default=0.8, gt=0, le=1)
    poe_autoscale: bool = True
    oversubscription_warn: float = Field(default=20, gt=0)
    fw_throughput_headroom: float = Field(default=0.3, ge=0, lt=1)
    power_model: Literal["datasheet", "legacy"] = "datasheet"
    legacy_power_w: LegacyPower = Field(default_factory=LegacyPower)
    poe_psu_efficiency: float = Field(default=0.9, gt=0, le=1)
    ups_power_factor: float = Field(default=0.9, gt=0, le=1)
    ups_headroom: float = Field(default=0.3, ge=0, le=5)
    rack_spare_ratio: float = Field(default=0.3, ge=0, le=5)
    patch_panel_ports: int = Field(default=24, gt=0)
    cable_manager_per_panel: float = Field(default=0.5, ge=0)
    copper_max_m: int = Field(default=90, gt=0)
    avg_cable_run_m_default: int = Field(default=40, gt=0)
    cable_box_m: int = Field(default=305, gt=0)
    fortios_default: str = "7.6.4"
    dual_psu_tiers_hint: list[int] = Field(default_factory=lambda: [1, 2])
    ip: IpRules


class FiberSpec(_Strict):
    """One backbone fibre type (e.g. OM4 / OS2) and the parts it is built from."""

    label: LText
    cable: str
    """Cable sold per metre (12 fibres)."""
    cable_fibers: int = Field(default=12, gt=0)
    housing_12: str
    housing_24: str
    cord: str
    """LC-LC duplex patch cord, housing ↔ transceiver."""
    transceiver: str
    max_10g_m: int = Field(default=400, gt=0)
    """Longest 10G link this fibre supports with ``transceiver``."""


class PassiveRules(_Strict):
    """Passive infrastructure (structured cabling, fibre backbone, rack power).

    Defaults are Corning Everon (copper) and Corning LANscape / FREEDM (fibre) part numbers.
    """

    jack: str = "KAXBSM-00104-C001-BP"
    jack_pack: int = Field(default=24, gt=0)
    panel: str = "MAXCSV-02408-C001"
    panel_ports: int = Field(default=24, gt=0)
    cable: str = "CCXEDB-DB047-C001-L7"
    cable_drum_m: int = Field(default=500, gt=0)
    cable_slack_m: int = Field(default=3, ge=0)
    """Service loop per link (both ends together)."""
    cord_rack: str = "CCAAGB-G5002-A010-C0"
    """Patch panel ↔ switch, also used at APs and cameras."""
    cord_user: str = "CCAAGB-G5002-A020-C0"
    """Work-area cord from a wall outlet to a user device."""
    outlet: str = "UAXCSE-U0201-C001"
    outlet_ports: int = Field(default=2, gt=0)
    manager: str = "XE005315637"
    fiber_default: Literal["auto", "om4", "os2"] = "auto"
    fiber: dict[str, FiberSpec] = Field(default_factory=dict)
    fiber_spare_ratio: float = Field(default=1.0, ge=0, le=10)
    """Spare fibres on top of the ones in use (1.0 = twice as many)."""
    fiber_slack_m: int = Field(default=20, ge=0)
    splice_protector: str = "HSP-45S100-1"
    pdu: str = "PDU-8-C13"
    pdu_outlets: int = Field(default=8, gt=0)


class Meta(_Strict):
    name: str = ""
    updated: str = ""
    currency: str = "UAH"
    notes: str = ""


class Catalog(_Strict):
    schema_version: int = 1
    meta: Meta = Field(default_factory=Meta)
    models: dict[str, Device]
    categories: dict[str, Category]
    firewall_ladder: list[str]
    firewall_fallback: LText
    ap_zones: dict[str, ApZone]
    tiers: dict[str, Tier]
    rules: Rules
    passive: PassiveRules = Field(default_factory=PassiveRules)

    @model_validator(mode="after")
    def _check_references(self) -> Catalog:
        problems: list[str] = []
        for key in CATEGORY_KEYS:
            if key not in self.categories:
                problems.append(f"categories: missing '{key}'")
        for key, cat in self.categories.items():
            for role in ("base", "premium"):
                model = getattr(cat, role)
                dev = self.models.get(model)
                if dev is None:
                    problems.append(f"categories.{key}.{role}: unknown model '{model}'")
                elif dev.kind != "switch":
                    problems.append(f"categories.{key}.{role}: '{model}' is not a switch")
        if not self.firewall_ladder:
            problems.append("firewall_ladder: must not be empty")
        for model in self.firewall_ladder:
            dev = self.models.get(model)
            if dev is None or dev.kind != "firewall":
                problems.append(f"firewall_ladder: '{model}' is not a firewall in models")
        for key, zone in self.ap_zones.items():
            dev = self.models.get(zone.model)
            if dev is None or dev.kind != "ap":
                problems.append(f"ap_zones.{key}: '{zone.model}' is not an access point in models")
        for tier_id in ("1", "2", "3", "4"):
            if tier_id not in self.tiers:
                problems.append(f"tiers: missing tier '{tier_id}'")
        seen_vlans: set[int] = set()
        for seg in self.rules.ip.segments:
            if seg.vlan in seen_vlans:
                problems.append(f"rules.ip.segments: duplicate VLAN {seg.vlan}")
            seen_vlans.add(seg.vlan)
        for key, spec in self.passive.fiber.items():
            for role in ("cable", "housing_12", "housing_24", "cord", "transceiver"):
                if getattr(spec, role) not in self.models:
                    problems.append(f"passive.fiber.{key}.{role}: unknown model '{getattr(spec, role)}'")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    # ---- convenience -------------------------------------------------------------------
    def device(self, model: str) -> Device:
        return self.models[model]

    def aps(self) -> dict[str, Device]:
        return {k: v for k, v in self.models.items() if v.kind == "ap"}

    def ups_models(self) -> list[tuple[str, Device]]:
        items = [(k, v) for k, v in self.models.items() if v.ups_va]
        return sorted(items, key=lambda kv: kv[1].ups_va or 0)

    def rack_models(self) -> list[tuple[str, Device]]:
        items = [(k, v) for k, v in self.models.items() if v.rack_size_u]
        return sorted(items, key=lambda kv: kv[1].rack_size_u or 0)

    def tier(self, tier_id: int | str) -> Tier:
        return self.tiers[str(tier_id)]

    @property
    def has_prices(self) -> bool:
        return any(d.price is not None for d in self.models.values())


class CatalogError(Exception):
    """Raised with a human-readable, multi-line explanation of what is wrong in a catalog."""

    def __init__(self, message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.details = details or []

    def __str__(self) -> str:
        if not self.details:
            return self.args[0]
        return self.args[0] + "\n" + "\n".join(f"  • {d}" for d in self.details)


def parse_version(text: str) -> tuple[int, ...]:
    """'7.6.1' -> (7, 6, 1); tolerant to suffixes like '7.6.1+' or 'v7.4'."""
    parts: list[int] = []
    for chunk in text.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _format_validation_error(err: ValidationError) -> list[str]:
    lines = []
    for item in err.errors():
        loc = ".".join(str(p) for p in item.get("loc", ()))
        msg = item.get("msg", "invalid value")
        lines.append(f"{loc}: {msg}" if loc else msg)
    return lines


def catalog_from_dict(data: dict[str, Any]) -> Catalog:
    try:
        return Catalog.model_validate(data)
    except ValidationError as err:
        raise CatalogError("Каталог містить помилки / Catalog is invalid:", _format_validation_error(err)) from err


def load_catalog(path: str | Path | None = None) -> Catalog:
    """Load and validate a catalog. Raises :class:`CatalogError` with friendly details."""
    p = Path(path) if path else DEFAULT_CATALOG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise CatalogError(f"Файл каталогу не знайдено: {p}") from err
    except json.JSONDecodeError as err:
        raise CatalogError(f"Некоректний JSON у {p.name}: рядок {err.lineno}, колонка {err.colno} — {err.msg}") from err
    return catalog_from_dict(raw)


def load_default_catalog() -> Catalog:
    return load_catalog(DEFAULT_CATALOG_PATH)


def with_missing_defaults(catalog: Catalog, default: Catalog | None = None) -> Catalog:
    """Add models (and the passive section) that a newer default catalog has and an older
    user catalog lacks, so new features keep working after an upgrade. User edits win."""
    base = default or load_default_catalog()
    data = catalog.model_dump(mode="json")
    added = False
    for model, dev in base.models.items():
        if model not in data["models"]:
            data["models"][model] = dev.model_dump(mode="json")
            added = True
    if not catalog.passive.fiber and base.passive.fiber:
        data["passive"] = base.passive.model_dump(mode="json")
        added = True
    return catalog_from_dict(data) if added else catalog


def save_catalog(catalog: Catalog, path: str | Path) -> None:
    data = catalog.model_dump(mode="json", exclude_none=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def catalog_with_overrides(catalog: Catalog, overrides: dict[str, Any]) -> Catalog:
    """Deep-merge ``overrides`` into a copy of ``catalog`` and re-validate."""
    data = catalog.model_dump(mode="json")

    def merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                merge(dst[key], value)
            else:
                dst[key] = copy.deepcopy(value)

    merge(data, overrides)
    return catalog_from_dict(data)


PROTOTYPE_COMPAT_OVERRIDES: dict[str, Any] = {
    "rules": {
        "variant_mode": "quantity",
        "power_model": "legacy",
        "bt_auto_upgrade": False,
        "poe_autoscale": False,
        "core_port_check": False,
    },
    "categories": {"camera_switch": {"premium": "FS-448E-POE"}},
    "models": {
        "FG-80F": {"firewall": {"max_switches": 16}},
        "FG-120G": {"firewall": {"max_switches": 32, "max_switches_fortios": []}},
        "FG-200G": {"firewall": {"max_switches": 64}},
        "FG-400G": {"firewall": {"max_switches": 128, "max_aps": 100000}},
    },
}
"""Overlay that reproduces the console prototype's assumptions (used by golden tests and the
``--compat`` CLI flag). It switches off checks the prototype did not have."""


def prototype_compat_catalog(catalog: Catalog | None = None) -> Catalog:
    base = catalog or load_default_catalog()
    compat = catalog_with_overrides(base, PROTOTYPE_COMPAT_OVERRIDES)
    # The prototype never limited firewalls by AP count.
    for model in compat.firewall_ladder:
        fw = compat.models[model].firewall
        if fw is not None:
            fw.max_aps = 10**6
    return compat
