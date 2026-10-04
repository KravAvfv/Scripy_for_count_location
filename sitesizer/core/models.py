"""Input and result data structures of the sizing engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Aggregation = Literal["auto", "yes", "no"]
Mode = Literal["quick", "extended"]

ADDON_KEYS = ("transceivers", "cabling", "rack", "ups", "licensing", "spares", "management")
"""Best-practice add-ons. ``None`` in :attr:`SiteInput.addons` means "use the default"
(on in extended mode, off in quick mode; spares also need a tier with a spare percentage)."""


class ApGroup(BaseModel):
    """One Wi-Fi zone: a placement type plus the number of APs in it."""

    model_config = ConfigDict(extra="ignore")

    zone: str = "low_density"
    qty: int = Field(default=1, ge=0, le=100_000)
    model: str | None = None
    """Explicit AP model; ``None`` = the zone's default model from the catalog."""
    clients_per_ap: int | None = Field(default=None, ge=0, le=1000)
    name: str = ""
    """Optional free-text zone name (e.g. "Склад, ряд A")."""


class IpOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_network: str = ""
    """e.g. ``10.50.0.0/16``; empty = sizes only, no addresses."""
    segments: dict[str, bool] = Field(default_factory=dict)
    """Segment id -> enabled; missing ids use the catalog default."""
    vlan_overrides: dict[str, int] = Field(default_factory=dict)


class SiteInput(BaseModel):
    """Everything the user enters about one location."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    name: str = "Локація"
    mode: Mode = "quick"
    sockets: int = Field(default=0, ge=0, le=1_000_000)
    cameras: int = Field(default=0, ge=0, le=1_000_000)
    ap_groups: list[ApGroup] = Field(default_factory=list)
    tier: int = Field(default=3, ge=1, le=4)
    aggregation: Aggregation = "auto"
    reserve: bool = False
    reserve_percent: float | None = Field(default=None, ge=0, le=500)
    redundant_psu: bool | None = None
    """Dual-PSU ("premium") switch models. ``None`` = not confirmed yet: the tier's
    suggestion is used and a confirmation prompt is raised."""

    wifi_clients_expected: int | None = Field(default=None, ge=0)
    camera_watts: float | None = Field(default=None, ge=0, le=90)
    fortios_version: str | None = None
    inspected_mbps: int | None = Field(default=None, ge=0)
    """Internet / inspected traffic that must pass the UTM engine (threat protection)."""
    max_cable_run_m: int | None = Field(default=None, ge=0, le=100_000)
    avg_cable_run_m: int | None = Field(default=None, ge=1, le=10_000)
    voice_phones: int = Field(default=0, ge=0)
    guest_clients: int = Field(default=0, ge=0)
    iot_devices: int = Field(default=0, ge=0)
    addons: dict[str, bool | None] = Field(default_factory=dict)
    ip: IpOptions = Field(default_factory=IpOptions)
    notes: str = ""

    @field_validator("aggregation", mode="before")
    @classmethod
    def _agg_alias(cls, v: object) -> object:
        """Accept the console prototype's answers: 'y', 'n', '' (auto)."""
        if isinstance(v, str):
            low = v.strip().lower()
            if low in ("y", "yes", "так", "т", "true"):
                return "yes"
            if low in ("n", "no", "ні", "н", "false"):
                return "no"
            if low in ("", "auto", "a", "авто"):
                return "auto"
        return v

    @property
    def total_aps_raw(self) -> int:
        return sum(g.qty for g in self.ap_groups)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Check:
    """A validation finding shown in the warnings panel."""

    severity: Severity
    code: str
    message: str
    hint: str = ""
    category: str = ""
    action: str = ""
    """Optional machine-readable action the GUI may offer (e.g. ``confirm_psu``)."""


@dataclass
class BomLine:
    """One line of the bill of materials."""

    group: str
    """Category key: ap, wifi_switch, access_switch, camera_switch, core_switch, firewall,
    tier, transceiver, cabling, rack, power, license, spare, reference."""
    category: str
    model: str
    qty: int | None
    reason: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    """Visual tags: ha, n+1, tier, addon, auto, spare, license, reference, eoo, unverified."""
    details: list[str] = field(default_factory=list)
    """Extra "why this line?" bullet points."""
    unit_price: float | None = None

    @property
    def total_price(self) -> float | None:
        if self.unit_price is None or self.qty is None:
            return None
        return self.unit_price * self.qty

    @property
    def is_reference(self) -> bool:
        return self.qty is None


@dataclass
class CategoryResult:
    """Sizing outcome for one switch category."""

    key: str
    count: int = 0
    model: str = ""
    endpoints: int = 0
    endpoints_per_switch: int = 0
    premium: bool = False
    upgraded_for_bt: bool = False
    poe_load_w: float = 0.0
    poe_budget_w: float = 0.0
    poe_per_switch_w: float = 0.0
    bt_needed: int = 0
    oversubscription: float = 0.0
    uplinks_per_switch: int = 1


@dataclass
class FirewallChoice:
    model: str
    count: int
    fits: bool
    switch_limit: int | None = None
    ap_limit: int | None = None
    reasons: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class PowerSummary:
    equipment_w: float = 0.0
    poe_w: float = 0.0
    total_w: float = 0.0
    heat_btu: float = 0.0
    ups_va: int = 0
    ups_model: str = ""
    legacy_w: float = 0.0
    breakdown: list[tuple[str, int, float]] = field(default_factory=list)
    """(model, qty, watts total)"""


@dataclass
class RackSummary:
    units_equipment: int = 0
    units_panels: int = 0
    units_managers: int = 0
    units_ups: int = 0
    units_other: int = 0
    units_total: int = 0
    units_with_spare: int = 0
    rack_model: str = ""
    rack_size_u: int = 0
    rack_count: int = 1
    idf_count: int = 1
    patch_panels: int = 0
    copper_endpoints: int = 0
    cable_m: int = 0
    cable_boxes: int = 0


@dataclass
class IpSegmentResult:
    id: str
    name: str
    vlan: int
    hosts: int
    prefix: int
    capacity: int
    network: str = ""
    gateway: str = ""
    dhcp_range: str = ""
    note: str = ""


@dataclass
class IpPlan:
    segments: list[IpSegmentResult] = field(default_factory=list)
    base_network: str = ""
    used_addresses: int = 0
    error: str = ""


@dataclass
class EffectiveCounts:
    """Endpoint counts after the growth reserve has been applied."""

    sockets: int = 0
    cameras: int = 0
    aps: int = 0
    ap_groups: list[ApGroup] = field(default_factory=list)
    wifi_clients: int | None = None
    reserve_factor: float = 1.0


@dataclass
class SiteResult:
    """Full output of :func:`sitesizer.core.sizing.size_site`."""

    input: SiteInput
    counts: EffectiveCounts
    categories: dict[str, CategoryResult]
    core: CategoryResult
    firewall: FirewallChoice | None
    tier_id: int
    dual_psu: bool
    dual_psu_confirmed: bool
    fortios_version: str
    bom: list[BomLine]
    checks: list[Check]
    power: PowerSummary
    rack: RackSummary
    ip_plan: IpPlan | None
    addons: dict[str, bool]

    # ---- convenience -------------------------------------------------------------------
    @property
    def edge_switch_count(self) -> int:
        return sum(c.count for c in self.categories.values())

    @property
    def total_switches(self) -> int:
        return self.edge_switch_count + self.core.count

    @property
    def has_equipment(self) -> bool:
        return any(line.qty for line in self.bom)

    def lines(self, group: str) -> list[BomLine]:
        return [line for line in self.bom if line.group == group]

    def count_by_severity(self) -> dict[Severity, int]:
        out = {s: 0 for s in Severity}
        for c in self.checks:
            out[c.severity] += 1
        return out

    @property
    def subtotal(self) -> float | None:
        priced = [line.total_price for line in self.bom if line.total_price is not None]
        return sum(priced) if priced else None
