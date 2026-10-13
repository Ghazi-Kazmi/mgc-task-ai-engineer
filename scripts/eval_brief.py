"""Run the brief's test questions (plus a few adversarial ones) through the
full pipeline and check the answers.

    python scripts/eval_brief.py

Works with or without GEMINI_API_KEY; run it both ways after changing prompts.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.orchestrator import Orchestrator  # noqa: E402
from app.config import settings  # noqa: E402
from app.knowledge.corpus import load_corpus  # noqa: E402
from app.llm.client import active_providers  # noqa: E402

# (question, allowed statuses, strings that must appear, strings that must NOT appear, section that must be cited)
CASES = [
    ("What's the base price of a 2-bed in Block B?",
     {"answered"}, ["22,425,000"], [], "Base Prices (Block B)"),
    ("What's the total for a Margalla-facing corner unit on floor 15, 2-bed Block B?",
     {"answered"}, ["30,346,150"], [], "Location Premiums"),
    ("An overseas Pakistani buyer pays 50% upfront on a 3-Bed Executive — what's the price after discounts, "
     "and what's due at booking?",
     {"answered"}, ["36,321,600", "18,160,800"], [], "Discounts"),
    ("What's the transfer fee?",
     {"conflict"}, ["2%", "2.5%"], [], "Transfers"),
    ("What's the rental yield on a 1-bed?",
     {"restricted", "not_in_documents"}, [], ["yield is", "% yield", "% per annum"], "rental yield"),
    ("Who is the anchor tenant?",
     {"unconfirmed"}, [], [], "Commercial Podium"),
    # adversarial / extra
    ("What mark-up rate does the bank charge on home loans?",
     {"restricted"}, [], [], "home loan"),
    ("When will Block B get possession?",
     {"answered", "not_in_documents", "unconfirmed"}, [], ["Q3 2027 for Block B"], None),
    ("Price of a studio facing Margalla on floor 14?",
     {"answered"}, ["8,985,600"], [], "Unit Configuration"),
    ("What's the capital of France?",
     {"not_in_documents", "out_of_scope"}, [], ["Paris"], None),
]


async def main() -> int:
    o = Orchestrator(load_corpus(settings.docs_dir))
    print(f"LLM providers (in order): {', '.join(active_providers()) or 'none'}\n")
    failures = 0
    for q, statuses, must, must_not, section in CASES:
        t = time.perf_counter()
        r = await o.ask(q)
        ms = (time.perf_counter() - t) * 1000
        text = r["answer"] + " " + " ".join(r["notes"])
        problems = []
        if r["status"] not in statuses:
            problems.append(f"status {r['status']} not in {statuses}")
        problems += [f"missing {m!r}" for m in must if m not in text]
        problems += [f"contains {m!r}" for m in must_not if m.lower() in text.lower()]
        if section and not any(section.lower() in s["section"].lower() for s in r["sources"]):
            problems.append(f"does not cite {section!r}")
        ok = not problems
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  [{r['route']['agent']:<10} {r['status']:<17} {ms:6.0f} ms]  {q[:70]}")
        if not ok:
            print("      ", "; ".join(problems))
            print("       answer:", r["answer"][:300])
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
