"""Deterministic price arithmetic. No LLM ever touches these numbers.

Rules applied (all from 02_price_list_payment_plan.md):
  * Location premiums are cumulative percentages of the base price.
  * Discounts are percentages of the base price only ("Location premiums are
    not discounted"). Stackable discounts are summed, then applied once.
  * Payment-plan shares are percentages of the final unit price.
All money is Decimal and rounded to whole rupees at the end of each step.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ONE_RUPEE = Decimal("1")
HUNDRED = Decimal("100")


def rupees(x: Decimal) -> Decimal:
    return x.quantize(ONE_RUPEE, rounding=ROUND_HALF_UP)


def pct_of(amount: Decimal, pct: Decimal) -> Decimal:
    return rupees(amount * pct / HUNDRED)


def fmt_pkr(x: Decimal) -> str:
    return f"PKR {rupees(x):,}"


def fmt_crore(x: Decimal) -> str:
    """Pakistani sales staff think in crore (10 million) / lakh (100,000)."""
    if x >= 10_000_000:
        return f"{x / 10_000_000:.2f} crore"
    return f"{x / 100_000:.1f} lakh"


@dataclass(frozen=True)
class Quote:
    base: Decimal
    premium_pct: Decimal
    premium_amount: Decimal
    discount_pct: Decimal
    discount_amount: Decimal
    price: Decimal            # base + premiums - discounts


def quote(base: Decimal, premium_pcts: list[Decimal], discount_pcts: list[Decimal]) -> Quote:
    p = sum(premium_pcts, Decimal(0))
    d = sum(discount_pcts, Decimal(0))
    p_amt = pct_of(base, p)
    d_amt = pct_of(base, d)
    return Quote(base, p, p_amt, d, d_amt, base + p_amt - d_amt)


def maintenance_advance(area_sqft: int, rate_psf: Decimal, months: Decimal) -> Decimal:
    return rupees(Decimal(area_sqft) * rate_psf * months)
