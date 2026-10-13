"""Compliance agent: topics the documents forbid sales staff from answering.

The FAQ contains explicit "sales staff must not ..." rules (rental-yield
projections, bank mark-up rates). For those topics a confident answer is
exactly the failure the business is worried about, so we skip the LLM and
answer with the documented policy, quoted and cited.

The rules themselves come from the documents: at startup we collect every
sentence containing "must not". The keyword lists below only decide which
rule a question is asking about.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..knowledge.corpus import Corpus, Section
from .base import AgentResult, Status

NAME = "compliance"

# topic -> (question keywords, word that identifies the governing FAQ section)
_TOPICS = {
    "rental yield / investment returns": (
        r"\byields?\b|rental (?:income|return)|\broi\b|return on investment|appreciat|capital gain|"
        r"resale value|investment return|how much rent",
        "rental yield",
    ),
    "bank mark-up / interest rates": (
        r"mark[\s-]?up|interest rate|mortgage rate|\bkibor\b|\bemi\b|loan rate",
        "home loan",
    ),
}


@dataclass(frozen=True)
class Rule:
    topic: str
    pattern: re.Pattern
    section: Section
    rule_sentence: str


def load_rules(corpus: Corpus) -> list[Rule]:
    rules = []
    for topic, (pattern, section_word) in _TOPICS.items():
        sec = next((s for s in corpus.sections if section_word in s.heading.lower()), None)
        if sec is None:
            continue
        text = " ".join(sec.text.replace("**", "").split())
        must_not = next((x for x in re.split(r"(?<=[.?!])\s+", text) if "must not" in x), None)
        if must_not:
            rules.append(Rule(topic, re.compile(pattern, re.I), sec, must_not))
    return rules


def match(question: str, rules: list[Rule]) -> Rule | None:
    return next((r for r in rules if r.pattern.search(question)), None)


def run(question: str, rule: Rule) -> AgentResult:
    # Drop the bold FAQ question line itself; quote the documented policy verbatim.
    body = re.sub(r"^.*?\?\s*", "", " ".join(rule.section.text.replace("**", "").split()), count=1)
    answer = (f"This is a restricted topic ({rule.topic}) - do not give the customer a figure. "
              f"MGC policy: \"{body}\"")
    return AgentResult(NAME, Status.RESTRICTED, answer, sources=[rule.section], context=[rule.section])
