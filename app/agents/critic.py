"""Critique layer: nothing reaches the salesperson without passing review.

Two tiers:

1. Rule checks (always run, ~0 ms):
   - cited:            a substantive answer must cite at least one real section
   - numbers_grounded: every number in the answer appears in a cited/context
                       section, in the question, or was produced by the calculator
   - conflict_disclosed: if the documents disagree on something relevant, the
                       answer must state every conflicting value
   - status_consistent: a "not in documents" answer must not carry figures

2. LLM review (only for answers the LLM wrote, if LLM_CRITIC is on):
   does the answer address the question, and is every claim supported by the
   sources? Pricing / compliance answers are deterministic, so they skip this.

The orchestrator uses the verdict: pass -> show; fail -> one retry with the
critic's feedback; fail again -> safe fallback (escalate to marketing manager).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from ..config import settings
from ..llm.client import generate_json
from .base import AgentResult, Status

_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    out = set()
    for raw in _NUM.findall(text):
        n = raw.replace(",", "")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    return out


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Critique:
    passed: bool
    checks: list[Check] = field(default_factory=list)
    llm_reviewed: bool = False

    def feedback(self) -> str:
        return "; ".join(f"{c.name}: {c.detail}" for c in self.checks if not c.passed)


class _LLMVerdict(BaseModel):
    addresses_question: bool
    fully_supported: bool
    problems: list[str]


_JUDGE = """You review answers written for a real-estate sales team. You are given the question, the
answer, and the only document sections the answer may rely on. Be strict but fair:
- addresses_question: does the answer actually respond to what was asked? (Saying the documents do not
  cover it, or that something is unconfirmed, counts as addressing it when that is true.)
- fully_supported: is every factual claim and number in the answer stated in the sections?
List concrete problems, or an empty list."""


def rule_checks(question: str, result: AgentResult) -> list[Check]:
    checks: list[Check] = []
    substantive = result.status not in (Status.NOT_IN_DOCUMENTS, Status.OUT_OF_SCOPE, Status.NEEDS_CLARIFICATION)

    if substantive:
        checks.append(Check("cited", bool(result.sources),
                            "" if result.sources else "answer has no source section"))

    full_text = result.answer + " " + " ".join(result.notes)
    allowed = _numbers(question) | result.computed_numbers
    for s in result.context + result.sources:
        allowed |= _numbers(s.text) | _numbers(s.heading)
    stray = sorted(_numbers(full_text) - allowed - _small_ordinals(full_text))
    checks.append(Check("numbers_grounded", not stray,
                        f"numbers not found in sources: {', '.join(stray)}" if stray else ""))

    if result.conflicts:
        missing = []
        for c in result.conflicts:
            for v in re.findall(r"(\d[\d.,]*%?) per ", c):
                if v not in result.answer:
                    missing.append(v)
        checks.append(Check("conflict_disclosed", not missing,
                            f"answer omits conflicting value(s): {', '.join(missing)}" if missing else ""))

    if result.status is Status.NOT_IN_DOCUMENTS:
        figures = _numbers(result.answer) - _numbers(question)
        checks.append(Check("status_consistent", not figures,
                            "a 'not in documents' answer should not quote figures" if figures else ""))
    return checks


def _small_ordinals(text: str) -> set[str]:
    # numbers used as list positions ("1." at line start) aren't claims
    return set(re.findall(r"^\s*(\d+)\.", text, re.M))


async def review(question: str, result: AgentResult) -> Critique:
    checks = rule_checks(question, result)
    llm_reviewed = False
    if result.used_llm and settings.llm_critic and all(c.passed for c in checks):
        sources = "\n\n".join(f"{s.doc_title} > {s.heading}\n{s.text}" for s in result.context)
        prompt = f"QUESTION: {question}\n\nANSWER: {result.answer}\n\nSECTIONS:\n{sources}"
        verdict = await generate_json(_JUDGE, prompt, _LLMVerdict)
        if verdict is not None:
            llm_reviewed = True
            checks.append(Check("addresses_question", verdict.addresses_question, "; ".join(verdict.problems)))
            checks.append(Check("supported_by_sources", verdict.fully_supported, "; ".join(verdict.problems)))
    return Critique(all(c.passed for c in checks), checks, llm_reviewed)
