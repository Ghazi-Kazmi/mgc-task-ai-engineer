"""Cross-document consistency audit.

At startup we pull out simple "<subject>: <number>" / "<subject> is <number>"
facts from every section and flag subjects that have different values in
different documents (e.g. the transfer fee: 2% in the price list, 2.5% in the
FAQ). Agents attach any conflict touching the sections they used, and the
critic rejects an answer that quotes only one side of it.

This is intentionally narrow: it only compares facts phrased the same way.
It catches the class of error that matters most here (a fee or percentage
that was updated in one document but not the other).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .corpus import Corpus, Section

# "Transfer fee (before possession): 2% of ..."   /   "Transfer fee is **2.5% of ..."
_FACT = re.compile(
    r"(?P<subject>[A-Z][A-Za-z ]{2,40}?)\s*(?:\([^)]*\))?\s*(?::|\bis\b)\s*\**\s*"
    r"(?P<value>(?:PKR\s*)?[\d][\d,.]*\s*%?)"
)


@dataclass(frozen=True)
class FactMention:
    subject: str
    value: str
    section: Section
    quote: str


@dataclass(frozen=True)
class Conflict:
    subject: str
    mentions: tuple[FactMention, ...]

    def section_ids(self) -> set[str]:
        return {m.section.id for m in self.mentions}

    def summary(self) -> str:
        parts = [f"{m.value} per {m.section.doc_title} ({m.section.heading})" for m in self.mentions]
        return f"The documents disagree on the {self.subject}: " + " vs ".join(parts) + "."


def _norm_subject(s: str) -> str:
    return " ".join(s.lower().split())


def _norm_value(v: str) -> str:
    return v.replace("PKR", "").replace(",", "").replace(" ", "").rstrip(".")


def find_conflicts(corpus: Corpus) -> list[Conflict]:
    by_subject: dict[str, list[FactMention]] = {}
    for sec in corpus.sections:
        text = " ".join(sec.text.split())
        for sentence in re.split(r"(?<=[.!?])\s+|\s-\s", text):
            for m in _FACT.finditer(sentence):
                subj = _norm_subject(m.group("subject"))
                by_subject.setdefault(subj, []).append(
                    FactMention(subj, m.group("value").strip(), sec, sentence.strip().lstrip("- "))
                )

    conflicts = []
    for subj, mentions in by_subject.items():
        docs = {m.section.doc_file for m in mentions}
        values = {_norm_value(m.value) for m in mentions}
        if len(docs) > 1 and len(values) > 1:
            conflicts.append(Conflict(subj, tuple(mentions)))
    return conflicts
