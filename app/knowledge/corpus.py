"""Load the markdown documents and split them into citable sections.

A section is the unit we cite: (document, section heading). FAQ-style bold
questions inside a section become their own sub-sections, so a citation can
point at "Frequently Asked > What is the rental yield?" instead of the whole FAQ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FAQ_Q = re.compile(r"^\*\*(.+\?)\*\*\s*$")


@dataclass(frozen=True)
class Section:
    id: str            # stable key, e.g. "02_price_list_payment_plan#Discounts"
    doc_file: str      # "02_price_list_payment_plan.md"
    doc_title: str     # first "# " heading of the file
    heading: str       # "Discounts" or "Frequently Asked > Is a home loan available?"
    text: str          # body text without the heading line

    def citation(self) -> dict:
        return {"document": self.doc_title, "file": self.doc_file, "section": self.heading}


@dataclass
class Corpus:
    sections: list[Section]

    def __post_init__(self) -> None:
        self._by_id = {s.id: s for s in self.sections}

    def get(self, section_id: str) -> Section:
        return self._by_id[section_id]

    def find(self, doc_prefix: str, heading: str) -> Section:
        """Look up a section by file prefix ("02_") and exact heading."""
        for s in self.sections:
            if s.doc_file.startswith(doc_prefix) and s.heading == heading:
                return s
        raise KeyError(f"No section {heading!r} in document {doc_prefix!r}")

    def has(self, doc_file: str, heading: str) -> bool:
        return any(s.doc_file == doc_file and s.heading == heading for s in self.sections)


def _split_doc(path: Path) -> list[Section]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.stem)

    # Split on "## " headings. Text before the first one (the subtitle) is the "Header" section.
    blocks: list[tuple[str, list[str]]] = [("Header", [])]
    for line in lines:
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            blocks.append((line[3:].strip(), []))
        else:
            blocks[-1][1].append(line)

    out: list[Section] = []
    for heading, body in blocks:
        # Break FAQ blocks into one section per bold question.
        sub: list[tuple[str, list[str]]] = [(heading, [])]
        for line in body:
            m = _FAQ_Q.match(line.strip())
            if m:
                sub.append((f"{heading} > {m.group(1)}", [line]))
            else:
                sub[-1][1].append(line)
        for h, b in sub:
            text = "\n".join(b).strip()
            if text:
                out.append(Section(f"{path.stem}#{h}", path.name, title, h, text))
    return out


def load_corpus(docs_dir: Path) -> Corpus:
    sections: list[Section] = []
    for path in sorted(docs_dir.glob("*.md")):
        sections.extend(_split_doc(path))
    if not sections:
        raise RuntimeError(f"No markdown documents found in {docs_dir}")
    return Corpus(sections)
