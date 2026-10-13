"""Policy agent: grounded Q&A over the documents (booking, transfers, refunds,
amenities, approvals, possession, ...).

Flow:  BM25 top sections -> drop weak hits -> attach known conflicts
       -> Gemini answers ONLY from those sections, as JSON with section ids
       -> ids are mapped back to real sections (the model can't invent a source).

No relevant section  -> "not in the documents" without calling the LLM.
No GEMINI_API_KEY / LLM failure -> extractive answer (best matching sentences, verbatim).
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..knowledge.conflicts import Conflict
from ..knowledge.corpus import Corpus, Section
from ..knowledge.retriever import BM25Retriever, tokenize
from ..llm.client import generate_json
from .base import ESCALATION, AgentResult, Status

NAME = "policy"

MIN_SCORE = 2.5          # below this BM25 score a section is noise for our corpus
RELATIVE_CUTOFF = 0.35   # keep hits scoring at least 35% of the best hit
TOP_K = 4
MIN_COVERAGE = 0.5       # extractive fallback only: the section must match most of the question

_SECTION_ID = re.compile(r"\s*[\(\[]S\d+(?:\s*,\s*S\d+)*[\)\]]")   # "(S1)", "[S1, S2]"
_UNCONFIRMED = re.compile(r"not been confirmed|no confirmed|unconfirmed|ongoing|applied for|"
                          r"subject to|in principle|not yet|pending", re.I)


class _LLMAnswer(BaseModel):
    status: Literal["answered", "conflict", "unconfirmed", "not_in_documents"]
    answer: str = Field(description="2-4 plain sentences for a salesperson")
    cited_sections: list[str] = Field(description="ids like S1, S2 of the sections the answer relies on")


SYSTEM = """You are the sales-desk assistant for MGC Developments (MGC Aurora Heights, Islamabad).
You answer salespeople's questions using ONLY the numbered document sections provided.

Rules:
1. Every fact in your answer must come from a provided section; list the ids you used in cited_sections.
2. If the sections do not contain the answer, set status "not_in_documents", say plainly that the documents
   don't cover it and that they should check with the marketing manager. Never guess or use outside knowledge.
3. If a section says something is not confirmed, pending, ongoing or applied for, set status "unconfirmed"
   and say so plainly. Do not speculate about the eventual outcome.
4. If sections disagree (see KNOWN CONFLICTS), set status "conflict", state each value with the document it
   comes from, mention which document is dated later if the dates are given, and tell them to confirm with
   management before quoting a customer. Never silently pick one value.
5. Do not calculate new numbers. Restate figures exactly as written.
6. Be concise: 2-4 sentences, plain language.
7. Never write section ids (S1, S2...) in the answer text; name documents in words if needed and put ids only in cited_sections."""


class PolicyAgent:
    def __init__(self, corpus: Corpus, retriever: BM25Retriever, conflicts: list[Conflict]) -> None:
        self.corpus = corpus
        self.retriever = retriever
        self.conflicts = conflicts
        # Document headers carry dates/versions ("Effective 1 April 2025", "Version 2.1, updated May 2025").
        self.headers = {s.doc_file: s for s in corpus.sections if s.heading == "Header"}
        self.doc_dates = {f: " ".join(s.text.replace("*", "").split()) for f, s in self.headers.items()}

    def retrieve(self, question: str) -> tuple[list[Section], list[Conflict]]:
        hits = self.retriever.search(question, k=TOP_K)
        if not hits or hits[0].score < MIN_SCORE:
            return [], []
        best = hits[0].score
        sections = [h.section for h in hits if h.score >= max(MIN_SCORE, RELATIVE_CUTOFF * best)]

        q_tokens = set(tokenize(question))
        relevant = [c for c in self.conflicts
                    if c.section_ids() & {s.id for s in sections} and q_tokens & set(tokenize(c.subject))]
        # Make sure both sides of a relevant conflict are in the context.
        for c in relevant:
            for m in c.mentions:
                if m.section.id not in {s.id for s in sections}:
                    sections.append(m.section)
        return sections, relevant

    async def run(self, question: str, feedback: str | None = None) -> AgentResult:
        sections, conflicts = self.retrieve(question)
        if not sections:
            if not settings.llm_enabled:
                return AgentResult(NAME, Status.NOT_IN_DOCUMENTS, ESCALATION)
            # Lexical search found nothing, but it misses synonyms ("gym" vs "Gymnasium").
            # The whole corpus is ~2.5k tokens, so let the model look at all of it.
            sections = [s for s in self.corpus.sections if s.heading != "Header"]

        conflict_notes = [c.summary() for c in conflicts]
        # Document dates/versions are shown to the model, so they are part of the checked context too.
        headers = [self.headers[f] for f in dict.fromkeys(s.doc_file for s in sections) if f in self.headers]
        llm = await self._ask_llm(question, sections, headers, conflict_notes, feedback)
        if llm is not None:
            return llm
        return self._extractive(question, sections, headers, conflicts)

    async def _ask_llm(self, question: str, sections: list[Section], headers: list[Section],
                       conflict_notes: list[str], feedback: str | None) -> AgentResult | None:
        ids = {f"S{i + 1}": s for i, s in enumerate(sections)}
        ctx = "\n\n".join(
            f"[{sid}] {s.doc_title} ({self.doc_dates.get(s.doc_file, '')}) > {s.heading}\n{s.text}"
            for sid, s in ids.items()
        )
        prompt = f"DOCUMENT SECTIONS:\n{ctx}\n\n"
        if conflict_notes:
            prompt += "KNOWN CONFLICTS:\n" + "\n".join(f"- {c}" for c in conflict_notes) + "\n\n"
        if feedback:
            prompt += f"A reviewer rejected your previous answer: {feedback}\nFix these problems.\n\n"
        prompt += f"QUESTION: {question}"

        out = await generate_json(SYSTEM, prompt, _LLMAnswer)
        if out is None:
            return None
        cited = [ids[c] for c in out.cited_sections if c in ids]
        status = Status(out.status)
        answer = _SECTION_ID.sub("", out.answer).strip()   # belt and braces for rule 7
        if status is Status.NOT_IN_DOCUMENTS and not answer:
            answer = ESCALATION
        return AgentResult(NAME, status, answer, sources=cited, context=sections + headers,
                           conflicts=conflict_notes, used_llm=True)

    def _extractive(self, question: str, sections: list[Section], headers: list[Section],
                    conflicts: list[Conflict]) -> AgentResult:
        """No-LLM fallback: return the best matching sentences verbatim, with their source."""
        if conflicts:
            c = conflicts[0]
            dates = " ".join(f"{m.section.doc_title}: \"{self.doc_dates.get(m.section.doc_file, '')}\""
                             for m in c.mentions)
            answer = (f"{c.summary()} Document dates - {dates} "
                      "Do not quote either figure until management confirms which one applies.")
            return AgentResult(NAME, Status.CONFLICT, answer, sources=[m.section for m in c.mentions],
                               context=sections + headers, conflicts=[c.summary()])

        if max(self.retriever.coverage(question, s) for s in sections) < MIN_COVERAGE:
            return AgentResult(NAME, Status.NOT_IN_DOCUMENTS, ESCALATION, context=sections)

        q = set(tokenize(question))
        best: list[tuple[int, str, Section]] = []
        for s in sections[:2]:
            for sent in re.split(r"(?<=[.?!])\s+", " ".join(s.text.replace("**", "").split())):
                overlap = len(q & set(tokenize(sent)))
                if overlap:
                    best.append((overlap, sent, s))
        best.sort(key=lambda x: x[0], reverse=True)
        picked = best[:2] or [(0, " ".join(sections[0].text.split())[:400], sections[0])]
        answer = " ".join(f"\"{sent}\"" for _, sent, _ in picked)
        status = Status.UNCONFIRMED if _UNCONFIRMED.search(answer) else Status.ANSWERED
        srcs = list({id(s): s for _, _, s in picked}.values())
        return AgentResult(NAME, status, answer, sources=srcs, context=sections,
                           notes=["Extractive answer (no LLM configured): quoted directly from the documents."])
