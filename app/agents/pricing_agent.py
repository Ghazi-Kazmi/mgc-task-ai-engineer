"""Pricing agent: prices, premiums, discounts and amounts due.

Flow:  question -> parse_request() (regex, deterministic)
               -> validate against brochure rules (block, floor, Margalla orientation)
               -> calculator.quote() (Decimal arithmetic)
               -> templated answer + calculation lines + citations.

The language model is deliberately not in this path. The vocabulary is small
(6 unit types, 2 blocks, a floor number, 4 premiums, 4 discounts), so a parser
is more reliable than an LLM here, and the maths is exact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..knowledge.corpus import Section
from ..knowledge.pricebook import PriceBook
from . import calculator as calc
from .base import AgentResult, Status

NAME = "pricing"

_NUM_WORD = {"one": "1", "two": "2", "three": "3", "four": "4"}
_BED = re.compile(r"\b(1|2|3|4|one|two|three|four)[\s-]*(?:bed(?:room)?s?|bhk|br)\b")


@dataclass
class PriceRequest:
    unit_type: str | None = None
    two_bed_unspecified: bool = False      # "2-bed" without "corner"/"standard"
    block: str | None = None
    floor: int | None = None
    corner: bool = False
    margalla: bool = False
    discounts: list[str] = field(default_factory=list)
    wants_booking: bool = False
    base_only: bool = False


def parse_request(question: str) -> PriceRequest:
    q = question.lower()
    r = PriceRequest()

    r.corner = "corner" in q
    r.margalla = "margalla" in q
    bed = _BED.search(q)
    beds = _NUM_WORD.get(bed.group(1), bed.group(1)) if bed else None
    if "penthouse" in q or beds == "4":
        r.unit_type = "4-Bed Penthouse"
    elif "executive" in q or beds == "3":
        r.unit_type = "3-Bed Executive"
    elif beds == "2":
        if r.corner:
            r.unit_type = "2-Bed Corner"
        else:
            r.unit_type = "2-Bed Standard"
            r.two_bed_unspecified = "standard" not in q
    elif beds == "1":
        r.unit_type = "1-Bed Standard"
    elif "studio" in q:
        r.unit_type = "Studio"

    if m := re.search(r"\bblock\s*([ab])\b", q):
        r.block = m.group(1).upper()
    if m := re.search(r"\b(?:floor|level)\s*(?:no\.?\s*|#\s*)?(\d{1,2})\b", q) or \
            re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*floor\b", q):
        r.floor = int(m.group(1))

    if re.search(r"full[\s-]*cash|cash payment|pays? (?:in |full )?cash|all cash", q):
        r.discounts.append("full_cash")
    if re.search(r"\b50\s*%|\bhalf\b.*\bupfront|fifty percent", q):
        r.discounts.append("upfront_50")
    if re.search(r"overseas|roshan|\brda\b|expat|abroad", q):
        r.discounts.append("overseas")
    if re.search(r"second unit|2nd unit|another unit|two units", q):
        r.discounts.append("second_unit")

    r.wants_booking = bool(re.search(r"booking|token|due at|down ?payment|upfront|payment plan|instal", q))
    r.base_only = "base price" in q and not (r.discounts or r.floor or r.corner or r.margalla or r.wants_booking)
    return r


def looks_like_pricing(question: str) -> bool:
    """Used by the router: a unit type plus a money word means 'compute something'."""
    r = parse_request(question)
    money = re.search(r"price|cost|total|how much|premium|discount|due|pay|quote|rate|amount|booking", question.lower())
    return r.unit_type is not None and bool(money)


class _Builder:
    """Collects answer lines, the sections used and every number we produced."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.calc: list[str] = []
        self.sources: dict[str, Section] = {}
        self.numbers: set[str] = set()
        self.notes: list[str] = []

    def use(self, *sections: Section) -> None:
        for s in sections:
            self.sources[s.id] = s

    def n(self, x: Decimal) -> str:
        """Format money and remember the value for the critic's number check."""
        self.numbers.add(str(calc.rupees(x)))
        return calc.fmt_pkr(x)

    def cr(self, x: Decimal) -> str:
        text = calc.fmt_crore(x)
        self.numbers.add(text.split()[0])
        return text


def run(question: str, book: PriceBook) -> AgentResult:
    req = parse_request(question)
    b = _Builder()

    if req.unit_type is None:
        return AgentResult(NAME, Status.NEEDS_CLARIFICATION,
                           "Which unit type? Options: " + ", ".join(book.unit_types) + ".",
                           sources=[book.units_section])

    # --- which block(s) -------------------------------------------------------
    available = book.blocks_for(req.unit_type)
    if req.block and req.block not in available:
        return AgentResult(NAME, Status.NEEDS_CLARIFICATION,
                           f"{req.unit_type} units are only in Block {' / '.join(available)}, not Block {req.block}.",
                           sources=[book.units_section])
    blocks = [req.block] if req.block else available
    if not req.block and len(available) == 1:
        b.notes.append(f"{req.unit_type} units exist only in Block {available[0]}, so Block {available[0]} is assumed.")

    # --- discounts sanity -------------------------------------------------------
    if {"full_cash", "upfront_50"} <= set(req.discounts):
        return AgentResult(NAME, Status.NEEDS_CLARIFICATION,
                           "Is the buyer paying full cash (12% off base) or 50% upfront (6% off base)? "
                           "The two payment discounts are alternatives.", sources=[book.discounts["full_cash"].section])

    # --- floor sanity -------------------------------------------------------------
    if req.floor is not None:
        lo, hi = book.podium_floors
        if req.floor > book.total_floors or req.floor < 1:
            return AgentResult(NAME, Status.NEEDS_CLARIFICATION,
                               f"The tower has {book.total_floors} floors; floor {req.floor} does not exist.",
                               sources=[book.overview_section])
        if lo <= req.floor <= hi:
            return AgentResult(NAME, Status.NEEDS_CLARIFICATION,
                               f"Floors {lo}-{hi} are the commercial podium; residential prices don't apply there. "
                               "Commercial payment plans are issued separately on request.",
                               sources=[book.podium_section])
        p_lo, p_hi = book.penthouse_floors
        if req.unit_type == "4-Bed Penthouse" and not (p_lo <= req.floor <= p_hi):
            return AgentResult(NAME, Status.NEEDS_CLARIFICATION,
                               f"Penthouses are only on floors {p_lo}-{p_hi}.", sources=[book.units_section])

    for block in blocks:
        _quote_block(req, block, book, b)

    if req.two_bed_unspecified:
        corner = book.price("2-Bed Corner", "B")
        if corner and "B" in blocks:
            b.use(corner.section)
            b.notes.append(f"'2-bed' read as 2-Bed Standard. Block B also has 2-Bed Corner units "
                           f"({corner.area_sqft:,} sq ft, base {b.n(corner.base_price)}).")

    return AgentResult(
        NAME, Status.ANSWERED, "\n".join(b.lines),
        sources=list(b.sources.values()), calculation=b.calc,
        computed_numbers=b.numbers, context=list(b.sources.values()), notes=b.notes,
    )


def _quote_block(req: PriceRequest, block: str, book: PriceBook, b: _Builder) -> None:
    unit = book.price(req.unit_type, block)
    b.use(unit.section)
    label = f"{unit.unit_type}, Block {block}"

    if req.base_only:
        b.lines.append(f"Base price of a {label} ({unit.area_sqft:,} sq ft): "
                       f"**{b.n(unit.base_price)}** (≈ {b.cr(unit.base_price)}).")
        b.calc.append(f"{label}: base {b.n(unit.base_price)} [Price List › {unit.section.heading}]")
        return

    # --- location premiums --------------------------------------------------
    prem: list[tuple[str, Decimal]] = []
    floor = req.floor
    if floor is None and unit.unit_type == "4-Bed Penthouse":
        lo, hi = book.penthouse_floors
        floor = lo
        b.notes.append(f"Penthouses are only on floors {lo}-{hi}, so the floor {lo}-{hi} premium always applies.")
    if floor is not None:
        for key, (lo, hi) in book.floor_bands.items():
            if lo <= floor <= hi:
                prem.append((f"Floors {lo}-{hi}", book.premiums[key].pct))

    if req.corner or unit.unit_type == "2-Bed Corner":
        prem.append(("Corner unit", book.premiums["corner"].pct))

    can_face_margalla = (block == book.margalla_block and unit.unit_type not in book.margalla_excluded)
    floor_faces = floor is not None and floor > book.margalla_above_floor
    if req.margalla:
        if not can_face_margalla:
            b.notes.append(f"Margalla-facing is not available for a {label} (brochure: only Block "
                           f"{book.margalla_block}, and not for {', '.join(sorted(book.margalla_excluded))}). "
                           "Margalla premium not applied.")
            b.use(book.units_section)
        elif floor is not None and not floor_faces:
            b.notes.append(f"The brochure only states that Block {book.margalla_block} units above floor "
                           f"{book.margalla_above_floor} face the Margalla range; confirm orientation for floor {floor}. "
                           "Margalla premium applied as requested.")
            b.use(book.units_section)
            prem.append(("Margalla-facing", book.premiums["margalla"].pct))
        else:
            prem.append(("Margalla-facing", book.premiums["margalla"].pct))
    elif can_face_margalla and floor_faces:
        prem.append(("Margalla-facing", book.premiums["margalla"].pct))
        b.notes.append(f"All Block {book.margalla_block} units above floor {book.margalla_above_floor} face the "
                       "Margalla range (brochure), so the Margalla premium applies.")
        b.use(book.units_section)

    if req.floor is None and unit.unit_type != "4-Bed Penthouse" and not req.margalla:
        b.notes.append("No floor given: floor and Margalla premiums are not included.")

    # --- discounts ------------------------------------------------------------
    disc = [(book.discounts[k].label, book.discounts[k].pct) for k in req.discounts]

    q = calc.quote(unit.base_price, [p for _, p in prem], [d for _, d in disc])
    b.numbers.update({str(q.premium_pct), str(q.discount_pct)})

    # --- calculation trace ----------------------------------------------------
    b.calc.append(f"Base price, {label}: {b.n(q.base)} [Price List › {unit.section.heading}]")
    if prem:
        b.use(book.premiums["corner"].section)
        parts = " + ".join(f"{n} {p}%" for n, p in prem)
        b.calc.append(f"Location premiums: {parts} = +{q.premium_pct}% of base = +{b.n(q.premium_amount)} "
                      f"[Price List › Location Premiums]")
    if disc:
        b.use(book.discounts["full_cash"].section)
        parts = " + ".join(f"{n} {p}%" for n, p in disc)
        b.calc.append(f"Discounts on base price: {parts} = -{q.discount_pct}% of base = -{b.n(q.discount_amount)} "
                      f"[Price List › Discounts]")
    if prem or disc:
        b.calc.append(f"Unit price = {b.n(q.base)}"
                      + (f" + {b.n(q.premium_amount)}" if prem else "")
                      + (f" - {b.n(q.discount_amount)}" if disc else "")
                      + f" = {b.n(q.price)}")

    what = "after discounts" if disc else "with premiums" if prem else "(no premiums or discounts apply)"
    b.lines.append(f"**{label}{f', floor {req.floor}' if req.floor else ''}: price {what} "
                   f"{b.n(q.price)}** (≈ {b.cr(q.price)}).")

    # --- what is due at booking ----------------------------------------------
    if req.wants_booking:
        b.use(book.plan_section)
        token = next(m for m in book.plan if "booking" in m.name.lower())
        token_amt = calc.pct_of(q.price, token.pct)
        if "upfront_50" in req.discounts:
            upfront = calc.pct_of(q.price, Decimal(50))
            b.lines.append(f"Due at booking with the 50%-upfront option: **{b.n(upfront)}** (50% of the unit price).")
            b.calc.append(f"50% upfront: 50% x {b.n(q.price)} = {b.n(upfront)}")
            b.notes.append(f"The documents define the 50%-upfront discount but not its full schedule. Under the "
                           f"standard plan the booking token is {token.pct}% ({b.n(token_amt)}); confirm with "
                           "sales management how the remaining 50% is scheduled.")
        else:
            b.lines.append(f"Due at booking (standard plan, {token.pct}% token): **{b.n(token_amt)}**.")
            b.calc.append(f"Booking token: {token.pct}% x {b.n(q.price)} = {b.n(token_amt)}")
            for m in book.plan:
                if m is token:
                    continue
                share = m.pct
                each = re.search(r"([\d.]+)% each", m.note)
                if each:
                    per = calc.pct_of(q.price, Decimal(each.group(1)))
                    b.calc.append(f"{m.name}: {each.group(1)}% each = {b.n(per)} each")
                else:
                    b.calc.append(f"{m.name}: {share}% = {b.n(calc.pct_of(q.price, share))}")

    # --- charges payable on top (never discounted) ------------------------------
    ch = book.charges
    b.use(book.charges_section)
    maint = calc.maintenance_advance(unit.area_sqft, ch["maintenance_psf"], ch["maintenance_months"])
    all_in = q.price + ch["parking"] + ch["utility"]
    b.lines.append(f"Plus mandatory charges: parking slot {b.n(ch['parking'])} and utility connection "
                   f"{b.n(ch['utility'])} -> {b.n(all_in)} in total; maintenance advance at possession "
                   f"{b.n(maint)} ({unit.area_sqft:,} sq ft x {ch['maintenance_psf']} x {ch['maintenance_months']} months).")
    if disc and len(disc) > 1:
        b.notes.append("Stacked discounts are added together and applied once to the base price; "
                       "the price list says premiums are never discounted.")
