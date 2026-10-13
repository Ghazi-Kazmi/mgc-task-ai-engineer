"""Orchestration layer: route -> agent -> critic -> (retry | fallback) -> response.

Routing is rules-first because rules are instant, free and predictable:
  1. greeting / thanks                       -> general
  2. restricted topic (yield, mark-up rates) -> compliance
  3. unit type + money word                  -> pricing
  4. otherwise                               -> policy
Only when the rules are unsure (a money word without a unit type, or a unit
type without a money word) do we ask Gemini to classify, when a key is set.

After the agent answers, the critic reviews it. If an LLM-written answer fails,
the agent gets one retry with the critic's feedback. If it still fails (or a
deterministic answer fails, which would be a bug), the user gets a safe
"check with the marketing manager" response instead of an unverified answer.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from ..knowledge.conflicts import find_conflicts
from ..knowledge.corpus import Corpus
from ..knowledge.pricebook import build_pricebook
from ..knowledge.retriever import BM25Retriever
from ..llm.client import generate_json
from . import compliance_agent, general_agent, pricing_agent
from .base import ESCALATION, AgentResult, Status
from .critic import Critique, review
from .policy_agent import PolicyAgent

_MONEY = re.compile(r"price|cost|total|how much|premium|discount|due|pay|quote|amount|cheap|expensive|budget", re.I)


@dataclass
class Route:
    agent: str
    method: Literal["rules", "llm", "default"]
    reason: str


class _LLMRoute(BaseModel):
    agent: Literal["pricing", "policy", "general"]
    reason: str


_ROUTER_SYSTEM = """Classify a salesperson's question about the MGC Aurora Heights apartment project.
- pricing: needs a price CALCULATED for a specific unit type (base price, premiums, discounts, amount due).
- policy: anything answered by looking up the documents (fees, booking rules, refunds, transfers, amenities,
  approvals, possession, general price-list facts not tied to one unit).
- general: greetings or questions unrelated to the project.
Return the agent and a short reason."""


class Orchestrator:
    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.pricebook = build_pricebook(corpus)
        self.conflicts = find_conflicts(corpus)
        self.rules = compliance_agent.load_rules(corpus)
        self.policy = PolicyAgent(corpus, BM25Retriever(corpus), self.conflicts)

    # ------------------------------------------------------------------ routing
    async def route(self, question: str) -> Route:
        if general_agent.is_smalltalk(question):
            return Route("general", "rules", "greeting / small talk")
        if rule := compliance_agent.match(question, self.rules):
            return Route("compliance", "rules", f"restricted topic: {rule.topic}")
        if pricing_agent.looks_like_pricing(question):
            return Route("pricing", "rules", "unit type + price wording")

        has_unit = pricing_agent.parse_request(question).unit_type is not None
        has_money = bool(_MONEY.search(question))
        if has_unit != has_money:   # exactly one signal: genuinely ambiguous
            llm = await generate_json(_ROUTER_SYSTEM, question, _LLMRoute)
            if llm:
                return Route(llm.agent, "llm", llm.reason)
        return Route("policy", "default" if has_unit != has_money else "rules", "document lookup")

    async def _run_agent(self, route: Route, question: str, feedback: str | None = None) -> AgentResult:
        if route.agent == "general":
            return general_agent.run(question)
        if route.agent == "compliance":
            return compliance_agent.run(question, compliance_agent.match(question, self.rules))
        if route.agent == "pricing":
            return pricing_agent.run(question, self.pricebook)
        return await self.policy.run(question, feedback)

    # ----------------------------------------------------------------- pipeline
    async def ask(self, question: str) -> dict:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}

        def lap(name: str, start: float) -> float:
            now = time.perf_counter()
            timings[name] = round((now - start) * 1000, 1)
            return now

        route = await self.route(question)
        t = lap("route", t0)

        result = await self._run_agent(route, question)
        t = lap("agent", t)
        critique = await review(question, result)
        t = lap("critic", t)

        attempts = 1
        if not critique.passed and result.used_llm:
            attempts = 2
            result = await self._run_agent(route, question, feedback=critique.feedback())
            critique = await review(question, result)
            t = lap("retry", t)

        withheld = not critique.passed
        if withheld:
            result = AgentResult(
                result.agent, Status.NOT_IN_DOCUMENTS,
                "I couldn't produce an answer I can verify against the documents. " + ESCALATION,
                sources=result.sources or result.context[:2],
            )
        timings["total"] = round((time.perf_counter() - t0) * 1000, 1)
        return _to_response(question, route, result, critique, attempts, withheld, timings)


def _to_response(question: str, route: Route, r: AgentResult, c: Critique, attempts: int,
                 withheld: bool, timings: dict) -> dict:
    return {
        "question": question,
        "answer": r.answer,
        "status": r.status.value,
        "sources": [s.citation() for s in r.sources],
        "calculation": r.calculation,
        "notes": r.notes,
        "conflicts": r.conflicts,
        "route": {"agent": route.agent, "method": route.method, "reason": route.reason},
        "critique": {
            "passed": c.passed,
            "withheld": withheld,
            "attempts": attempts,
            "llm_reviewed": c.llm_reviewed,
            "checks": [{"name": k.name, "passed": k.passed, "detail": k.detail} for k in c.checks],
        },
        "used_llm": r.used_llm,
        "timings_ms": timings,
    }
