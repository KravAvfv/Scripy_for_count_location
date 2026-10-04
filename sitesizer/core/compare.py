"""Side-by-side comparison of two sizing results (e.g. Tier 2 vs Tier 1)."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SiteResult


@dataclass
class DiffRow:
    group: str
    category: str
    model: str
    qty_a: int
    qty_b: int
    price: float | None

    @property
    def delta(self) -> int:
        return self.qty_b - self.qty_a

    @property
    def status(self) -> str:
        if self.qty_a == 0:
            return "added"
        if self.qty_b == 0:
            return "removed"
        if self.delta:
            return "changed"
        return "same"

    @property
    def cost_delta(self) -> float | None:
        return None if self.price is None else self.price * self.delta


def diff_results(a: SiteResult, b: SiteResult) -> list[DiffRow]:
    """Rows keyed by (group, model), in BoM order of ``a`` then new rows of ``b``."""
    rows: dict[tuple[str, str], DiffRow] = {}
    for side, result in (("a", a), ("b", b)):
        for line in result.bom:
            if line.qty is None:
                continue
            key = (line.group, line.model)
            row = rows.get(key)
            if row is None:
                row = DiffRow(
                    group=line.group, category=line.category, model=line.model, qty_a=0, qty_b=0, price=line.unit_price
                )
                rows[key] = row
            if side == "a":
                row.qty_a += line.qty
            else:
                row.qty_b += line.qty
    return list(rows.values())


def cost_delta(rows: list[DiffRow]) -> float | None:
    values = [r.cost_delta for r in rows if r.cost_delta is not None]
    return sum(values) if values else None
