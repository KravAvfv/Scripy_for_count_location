"""Input and result data structures of the sizing engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Aggregation = Literal["auto", "yes", "no"]
Mode = Literal["quick", "extended"]
FiberChoice = Literal["auto", "om4", "os2"]

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


class CustomSegment(BaseModel):
    """A VLAN added by the user (not in the catalog)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = "VLAN"
    vlan: int = Field(default=100, ge=1, le=4094)
    hosts: int = Field(default=10, ge=0, le=1_000_000)
    dhcp: bool = True


class IpOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_network: str = ""
    """e.g. ``10.50.0.0/16``; empty = sizes only, no addresses. Ignored when the location ID is set."""
    segments: dict[str, bool] = Field(default_factory=dict)
    """Segment id -> enabled; missing ids use the catalog default."""
    vlan_overrides: dict[str, int] = Field(default_factory=dict)
    name_overrides: dict[str, str] = Field(default_factory=dict)
    prefix_overrides: dict[str, int] = Field(default_factory=dict)
    """Segment id -> prefix length (e.g. 23 for a /23)."""
    custom: list[CustomSegment] = Field(default_factory=list)


class BomOverride(BaseModel):
    """Manual edits of one BoM line (``None`` = keep the calculated value)."""

    model_config = ConfigDict(extra="ignore")

    qty: int | None = Field(default=None, ge=0, le=1_000_000)
    price: float | None = Field(default=None, ge=0)
    price_min: float | None = Field(default=None, ge=0)


Section = Literal["sks", "network", "works"]


class CustomLine(BaseModel):
    """A line added to the BoM by hand (from the catalog or free text)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    model: str = ""
    """Catalog model id or a free part number."""
    name: str = ""
    code: str = ""
    unit: str = "шт."
    qty: int = Field(default=1, ge=0, le=1_000_000)
    price: float | None = Field(default=None, ge=0)
    price_min: float | None = Field(default=None, ge=0)
    section: Section = "network"


class BomEdits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overrides: dict[str, BomOverride] = Field(default_factory=dict)
    """BoM line key (``group:model``) -> manual values."""
    custom: list[CustomLine] = Field(default_factory=list)


RackExtraKind = Literal["manager", "panel", "shelf", "blank", "odf", "custom", "device"]


class RackProps(BaseModel):
    model_config = ConfigDict(extra="ignore")

    size_u: int | None = Field(default=None, ge=4, le=60)
    floor: int | None = Field(default=None, ge=-10, le=300)
    letter: str | None = None
    name: str | None = None


class RackPos(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rack: str
    u: int = Field(ge=1, le=60)


class RackExtra(BaseModel):
    """An item placed by the user: organizer, patch panel, shelf, blank, a catalog device or a custom one."""

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: RackExtraKind = "manager"
    rack: str
    u: int = Field(ge=1, le=60)
    height: int = Field(default=1, ge=1, le=10)
    label: str = ""
    model: str = ""
    """Catalog model of a ``device`` (it is added to the bill of materials)."""


class RackLayout(BaseModel):
    """Manual cabinet layout on top of the automatic one."""

    model_config = ConfigDict(extra="ignore")

    added: list[str] = Field(default_factory=list)
    """Keys of cabinets added by the user."""
    removed: list[str] = Field(default_factory=list)
    """Keys of automatic cabinets removed by the user (their items move elsewhere)."""
    props: dict[str, RackProps] = Field(default_factory=dict)
    positions: dict[str, RackPos] = Field(default_factory=dict)
    """Item id -> where the user dropped it."""
    hidden: list[str] = Field(default_factory=list)
    """Automatic passive items removed by the user."""
    extras: list[RackExtra] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    """Item id -> name given by the user (replaces the automatic ``BO123-5B-ASW01``)."""

    @property
    def is_empty(self) -> bool:
        return not (
            self.added or self.removed or self.props or self.positions or self.hidden or self.extras or self.labels
        )


class SiteInput(BaseModel):
    """Everything the user enters about one location."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    name: str = "Локація"
    location_code: str = Field(default="", max_length=32)
    """Short site code used in device and cabinet names (e.g. ``BO123``)."""
    location_id: int | None = Field(default=None, ge=0, le=255)
    """Second octet of the site's addresses; builds the IP table (``10.<ID>.<VLAN>.0/24``)."""
    floors: int = Field(default=1, ge=1, le=300)
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
    rack_size_u: int = Field(default=0, ge=0, le=60)
    """Preferred cabinet size (24 or 42 U); 0 = pick automatically."""
    closets: int = Field(default=0, ge=0, le=50)
    """Number of telecom closets (main + remote); 0 = derive from the longest cable run."""
    fiber_type: FiberChoice = "auto"
    fiber_backbone_m: int | None = Field(default=None, ge=1, le=100_000)
    """Average fibre run from the main rack to each remote closet; ``None`` = estimate."""
    guest_clients: int = Field(default=0, ge=0)
    iot_devices: int = Field(default=0, ge=0)
    addons: dict[str, bool | None] = Field(default_factory=dict)
    ip: IpOptions = Field(default_factory=IpOptions)
    bom: BomEdits = Field(default_factory=BomEdits)
    layout: RackLayout = Field(default_factory=RackLayout)
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
    price_min: float | None = None
    """Second (lower) price column of the Excel template (column E)."""
    key: str = ""
    """Stable id used for manual overrides (``group:model``, ``#n`` suffix for duplicates)."""
    code: str = ""
    unit: str = "шт."
    calc_qty: int | None = None
    """Calculated quantity before a manual override."""
    manual: bool = False

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
class RackItem:
    """One device or panel placed in a cabinet. ``u`` is the lowest unit it occupies (1 = bottom)."""

    u: int
    height: int
    label: str
    group: str
    """Colour key: a BoM group, or ``panel``, ``manager``, ``fiber``, ``pdu``, ``blank``, ``shelf``."""
    model: str = ""
    id: str = ""
    """Stable id used by the manual layout (``access_switch:3``, ``panel:access_switch:3:1``...)."""
    manual: bool = False
    """Placed by the user (drag & drop) rather than automatically."""
    extra: bool = False
    """Added by the user."""

    @property
    def top(self) -> int:
        return self.u + self.height - 1


@dataclass
class RackPlan:
    """Front elevation of one cabinet."""

    name: str
    role: str
    """``mdf`` (main) or ``idf`` (remote closet)."""
    size_u: int
    model: str
    items: list[RackItem] = field(default_factory=list)
    key: str = ""
    floor: int = 1
    letter: str = "A"

    @property
    def tag(self) -> str:
        """Short cabinet tag like ``5B``."""
        return f"{self.floor}{self.letter}"

    def free_slot(self, height: int, start: int | None = None) -> int | None:
        """Highest unit where ``height`` units are free (searching top-down), or ``None``."""
        occupied = [False] * (self.size_u + 2)
        for it in self.items:
            for u in range(max(1, it.u), min(self.size_u, it.top) + 1):
                occupied[u] = True
        top = min(self.size_u, start or self.size_u)
        for hi in range(top, height - 1, -1):
            lo = hi - height + 1
            if all(not occupied[u] for u in range(lo, hi + 1)):
                return lo
        return None

    @property
    def used_u(self) -> int:
        return sum(i.height for i in self.items)

    @property
    def free_u(self) -> int:
        return max(0, self.size_u - self.used_u)


@dataclass
class PassiveSummary:
    """Structured cabling and fibre backbone quantities."""

    copper_links: int = 0
    sockets: int = 0
    device_links: int = 0
    """APs and cameras (one outlet each)."""
    jacks: int = 0
    jack_packs: int = 0
    panels: int = 0
    outlets: int = 0
    cords_rack: int = 0
    cords_user: int = 0
    cable_m: int = 0
    cable_drums: int = 0
    managers: int = 0
    fiber_type: str = ""
    fiber_auto: bool = True
    fiber_links: int = 0
    fiber_cables: int = 0
    fiber_m: int = 0
    fiber_cores: int = 0
    housings_12: int = 0
    housings_24: int = 0
    fiber_cords: int = 0
    splices: int = 0
    pdus: int = 0
    backbone_m: list[int] = field(default_factory=list)
    """Estimated fibre run per remote closet."""


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
    plans: list[RackPlan] = field(default_factory=list)
    passive: PassiveSummary = field(default_factory=PassiveSummary)
    layout_problems: list[str] = field(default_factory=list)
    """Conflicts of the manual layout (overlaps, items that did not fit)."""


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
    mask: str = ""
    custom: bool = False
    dhcp: bool = True
    too_small: bool = False


@dataclass
class IpPlan:
    segments: list[IpSegmentResult] = field(default_factory=list)
    base_network: str = ""
    used_addresses: int = 0
    error: str = ""
    location_id: int | None = None
    template: str = ""

    @property
    def has_addresses(self) -> bool:
        return any(s.network for s in self.segments)


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
