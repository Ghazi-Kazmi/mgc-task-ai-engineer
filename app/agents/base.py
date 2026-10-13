"""Shared types passed between the orchestrator, agents and the critic."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..knowledge.corpus import Section


class Status(str, Enum):
    ANSWERED = "answered"
    CONFLICT = "conflict"                 # documents disagree; both values shown
    UNCONFIRMED = "unconfirmed"           # documents say it is not confirmed / pending
    NOT_IN_DOCUMENTS = "not_in_documents"
    RESTRICTED = "restricted"             # documents forbid staff from answering (yield, mark-up)
    NEEDS_CLARIFICATION = "needs_clarification"
    OUT_OF_SCOPE = "out_of_scope"


ESCALATION = "I don't have that in the MGC documents. Please check with the marketing manager before quoting the customer."


@dataclass
class AgentResult:
    agent: str
    status: Status
    answer: str
    sources: list[Section] = field(default_factory=list)
    # Pricing agent: human-readable calculation lines and every number it produced.
    calculation: list[str] = field(default_factory=list)
    computed_numbers: set[str] = field(default_factory=set)
    # Sections the agent was given (critic checks claims against these).
    context: list[Section] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)   # assumptions / caveats shown to the user
