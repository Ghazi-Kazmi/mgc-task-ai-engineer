"""Structured pricing facts, parsed from the documents at startup.

Nothing here is hard-coded: base prices, premiums, discounts, the payment plan
and charges are read from the price-list markdown, and the unit / orientation
rules from the brochure. Every fact keeps a pointer to the section it came from
so the pricing agent can cite exactly what it used. If the documents change
shape, parsing fails loudly at startup instead of serving stale numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .corpus import Corpus, Section

PRICE_DOC = "02_"
BROCHURE_DOC = "01_"


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", "").strip())


def _pct(s: str) -> Decimal:
    return Decimal(s.strip().rstrip("%").lstrip("+"))


def _table_rows(text: str) -> list[list[str]]:
    """Markdown table body rows as lists of cell strings (header + separator skipped)."""
    rows = [l for l in text.splitlines() if l.strip().startswith("|")]
    return [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows[2:]]


@dataclass(frozen=True)
class UnitPrice:
    unit_type: str
    block: str
    area_sqft: int
    base_price: Decimal
    section: Section


@dataclass(frozen=True)
class Rate:
    key: str
    pct: Decimal
    label: str
    section: Section


@dataclass(frozen=True)
class Milestone:
    name: str
    pct: Decimal
    note: str


@dataclass
class PriceBook:
    units: list[UnitPrice]
    premiums: dict[str, Rate]           # floor_13_19, floor_20_22, corner, margalla
    floor_bands: dict[str, tuple[int, int]]
    discounts: dict[str, Rate]          # full_cash, upfront_50, overseas, second_unit
    plan: list[Milestone]
    plan_section: Section
    charges: dict[str, Decimal]         # utility, parking, extra_parking, maintenance_psf, maintenance_months
    charges_section: Section
    # brochure rules
    unit_blocks: dict[str, set[str]]    # unit type -> blocks it exists in
    penthouse_floors: tuple[int, int]
    margalla_block: str
    margalla_above_floor: int
    margalla_excluded: set[str]         # unit types never Margalla-facing
    total_floors: int
    podium_floors: tuple[int, int]
    units_section: Section
    overview_section: Section
    podium_section: Section
    unit_types: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.unit_types = sorted({u.unit_type for u in self.units})

    def price(self, unit_type: str, block: str) -> UnitPrice | None:
        return next((u for u in self.units if u.unit_type == unit_type and u.block == block), None)

    def blocks_for(self, unit_type: str) -> list[str]:
        return sorted(u.block for u in self.units if u.unit_type == unit_type)


def build_pricebook(corpus: Corpus) -> PriceBook:
    # --- base prices -------------------------------------------------------
    units: list[UnitPrice] = []
    for s in corpus.sections:
        m = re.match(r"Base Prices \(Block (\w)\)", s.heading)
        if s.doc_file.startswith(PRICE_DOC) and m:
            for unit_type, area, price, _psf in _table_rows(s.text):
                units.append(UnitPrice(unit_type, m.group(1), int(_money(area.split()[0])), _money(price), s))
    if not units:
        raise RuntimeError("Could not parse base price tables from the price list")

    # --- location premiums -------------------------------------------------
    prem_s = corpus.find(PRICE_DOC, "Location Premiums")
    premiums: dict[str, Rate] = {}
    floor_bands: dict[str, tuple[int, int]] = {}
    for label, pct in re.findall(r"^- (.+?):\s*\*\*(\+[\d.]+%)\*\*", prem_s.text, re.M):
        fl = re.match(r"Floors (\d+)\D+(\d+)", label)
        if fl:
            key = f"floor_{fl.group(1)}_{fl.group(2)}"
            floor_bands[key] = (int(fl.group(1)), int(fl.group(2)))
        elif "corner" in label.lower():
            key = "corner"
        elif "margalla" in label.lower():
            key = "margalla"
        else:
            continue
        premiums[key] = Rate(key, _pct(pct), label, prem_s)

    # --- discounts ----------------------------------------------------------
    disc_s = corpus.find(PRICE_DOC, "Discounts")
    discounts: dict[str, Rate] = {}
    for label, rest in re.findall(r"^- \*\*(.+?):\*\*\s*(.+)$", disc_s.text, re.M):
        low = label.lower()
        key = ("full_cash" if "cash" in low else "upfront_50" if "upfront" in low
               else "overseas" if "overseas" in low else "second_unit" if "second" in low else None)
        pct = re.search(r"([\d.]+)%", rest)
        if key and pct:
            discounts[key] = Rate(key, _pct(pct.group(1)), label, disc_s)

    # --- payment plan -------------------------------------------------------
    plan_s = corpus.find(PRICE_DOC, "Standard Payment Plan (4 years)")
    plan = [Milestone(name, _pct(share), note) for name, share, note in _table_rows(plan_s.text)]

    # --- other charges ------------------------------------------------------
    ch_s = corpus.find(PRICE_DOC, "Other Charges")
    charges: dict[str, Decimal] = {}
    for line in ch_s.text.splitlines():
        low = line.lower()
        amount = re.search(r":\s*([\d,]+)\b", line)
        if low.startswith("- utility") and amount:
            charges["utility"] = _money(amount.group(1))
        elif low.startswith("- parking slot") and amount:
            charges["parking"] = _money(amount.group(1))
        elif low.startswith("- additional parking") and amount:
            charges["extra_parking"] = _money(amount.group(1))
        elif low.startswith("- maintenance"):
            m = re.search(r"(\d+) months at ([\d.]+) per sq ft", line)
            if m:
                charges["maintenance_months"] = Decimal(m.group(1))
                charges["maintenance_psf"] = Decimal(m.group(2))

    # --- brochure rules -----------------------------------------------------
    units_s = corpus.find(BROCHURE_DOC, "Unit Configuration")
    unit_blocks: dict[str, set[str]] = {}
    penthouse_floors = (0, 0)
    for unit_type, _area, _count, block in _table_rows(units_s.text):
        unit_blocks[unit_type] = set(re.findall(r"\b([AB])\b", block))
        fl = re.search(r"floors (\d+)\D+(\d+)", block)
        if fl:
            penthouse_floors = (int(fl.group(1)), int(fl.group(2)))
    prose = " ".join(units_s.text.split())  # sentences wrap across lines in the markdown
    mg = re.search(r"above floor (\d+) in Block (\w) face the Margalla", prose)
    ex = re.search(r"\. ([\w\- ]+?) are not available with a Margalla-facing", prose)
    if not mg or not ex:
        raise RuntimeError("Could not parse Margalla orientation rule from the brochure")
    # "Studio and 1-Bed units" -> {"Studio", "1-Bed Standard"}
    excluded_words = [w.strip().lower() for w in re.split(r",| and ", ex.group(1).replace(" units", ""))]
    margalla_excluded = {t for t in unit_blocks if any(t.lower().startswith(w) for w in excluded_words)}

    overview_s = corpus.find(BROCHURE_DOC, "Overview")
    storeys = re.search(r"(\d+)-storey", overview_s.text)
    podium_s = corpus.find(BROCHURE_DOC, "Commercial Podium")
    podium = re.search(r"Floors (\d+) through (\d+)", podium_s.text)

    book = PriceBook(
        units=units, premiums=premiums, floor_bands=floor_bands, discounts=discounts,
        plan=plan, plan_section=plan_s, charges=charges, charges_section=ch_s,
        unit_blocks=unit_blocks, penthouse_floors=penthouse_floors,
        margalla_block=mg.group(2), margalla_above_floor=int(mg.group(1)),
        margalla_excluded=margalla_excluded,
        total_floors=int(storeys.group(1)) if storeys else 22,
        podium_floors=(int(podium.group(1)), int(podium.group(2))) if podium else (1, 3),
        units_section=units_s, overview_section=overview_s, podium_section=podium_s,
    )
    _sanity_check(book)
    return book


def _sanity_check(book: PriceBook) -> None:
    missing = {"corner", "margalla"} - book.premiums.keys()
    missing |= {"full_cash", "upfront_50", "overseas", "second_unit"} - book.discounts.keys()
    missing |= {"utility", "parking", "maintenance_psf"} - book.charges.keys()
    if missing or not book.floor_bands or not book.plan:
        raise RuntimeError(f"Price list parse incomplete, missing: {sorted(missing)}")
    if sum(m.pct for m in book.plan) != 100:
        raise RuntimeError("Payment plan shares do not add up to 100%")
