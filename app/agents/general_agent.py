"""General agent: greetings, thanks and clearly off-topic questions.

Answers instantly, without retrieval or an LLM, and tells the user what the
assistant can do.
"""
from __future__ import annotations

import re

from .base import AgentResult, Status

NAME = "general"

_GREETING = re.compile(r"^\s*(hi|hello|hey|salam|assalam[\w ]*|aoa|thanks|thank you|ok|okay|good (morning|evening))\W*$", re.I)

HELP = ("I answer questions about MGC Aurora Heights from the official documents (brochure, price list & "
        "payment plan, booking policy FAQ) - for example unit prices with premiums and discounts, what's due "
        "at booking, transfers, refunds, possession and amenities. Every answer names its source.")


def is_smalltalk(question: str) -> bool:
    return bool(_GREETING.match(question))


def run(question: str) -> AgentResult:
    return AgentResult(NAME, Status.OUT_OF_SCOPE, HELP)
