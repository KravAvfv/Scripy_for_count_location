"""Optional pricing: line totals, subtotal, discount and VAT."""

from __future__ import annotations

from dataclasses import dataclass

from .models import BomLine


@dataclass
class PriceSummary:
    currency: str
    subtotal: float
    discount_pct: float
    discount: float
    net: float
    vat_pct: float
    vat: float
    total: float
    priced_lines: int
    unpriced_lines: int

    @property
    def complete(self) -> bool:
        return self.unpriced_lines == 0


def summarize_prices(
    bom: list[BomLine], currency: str = "UAH", discount_pct: float = 0.0, vat_pct: float = 0.0
) -> PriceSummary | None:
    """Totals for priced lines; ``None`` when no line has a price (price columns stay hidden)."""
    purchasable = [line for line in bom if line.qty]
    priced = [line for line in purchasable if line.unit_price is not None]
    if not priced:
        return None
    subtotal = sum(line.total_price or 0.0 for line in priced)
    discount = subtotal * max(0.0, min(discount_pct, 100.0)) / 100
    net = subtotal - discount
    vat = net * max(0.0, vat_pct) / 100
    return PriceSummary(
        currency=currency,
        subtotal=round(subtotal, 2),
        discount_pct=discount_pct,
        discount=round(discount, 2),
        net=round(net, 2),
        vat_pct=vat_pct,
        vat=round(vat, 2),
        total=round(net + vat, 2),
        priced_lines=len(priced),
        unpriced_lines=len(purchasable) - len(priced),
    )


def format_money(value: float | None, currency: str = "UAH") -> str:
    if value is None:
        return "—"
    text = f"{value:,.2f}".replace(",", " ")
    symbol = {"UAH": "₴", "USD": "$", "EUR": "€"}.get(currency, currency)
    return f"{text} {symbol}"
