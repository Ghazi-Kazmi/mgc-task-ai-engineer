"""BM25 retrieval over document sections.

The corpus is ~30 short sections, so a 40-line lexical ranker is fast (<1 ms),
deterministic and easy to debug. Embeddings would be the next step if the
document set grew or queries got more paraphrased.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .corpus import Corpus, Section

_STOP = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are", "be", "what",
    "whats", "what's", "who", "how", "much", "does", "do", "can", "i", "we", "it", "its",
    "this", "that", "with", "by", "at", "as", "there", "any", "my", "me", "our", "will",
    "when", "where", "which", "why", "if", "get", "should", "would", "could", "about", "tell", "please",
}


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", text.lower().replace("’", "'"))
    out = []
    for t in tokens:
        if t in _STOP:
            continue
        # crude plural folding: "instalments" -> "instalment", "fees" -> "fee"
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


@dataclass(frozen=True)
class Hit:
    section: Section
    score: float


class BM25Retriever:
    def __init__(self, corpus: Corpus, k1: float = 1.4, b: float = 0.75) -> None:
        self.corpus = corpus
        self.k1, self.b = k1, b
        # Heading counted twice: it is the best summary of what a section is about.
        self.docs = [tokenize(f"{s.heading} {s.heading} {s.text}") for s in corpus.sections]
        self.tf = [Counter(d) for d in self.docs]
        self.avgdl = sum(len(d) for d in self.docs) / len(self.docs)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 5) -> list[Hit]:
        q = tokenize(query)
        scored = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scored.append(Hit(self.corpus.sections[i], s))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]

    def coverage(self, query: str, section: Section) -> float:
        """Share of the query's IDF weight that appears in the section (0..1).
        Words the corpus has never seen get the maximum IDF, so "capital of France"
        matching only "Capital Development Authority" scores low."""
        q = set(tokenize(query))
        if not q:
            return 0.0
        max_idf = max(self.idf.values())
        words = set(tokenize(f"{section.heading} {section.text}"))
        total = sum(self.idf.get(t, max_idf) for t in q)
        return sum(self.idf.get(t, max_idf) for t in q if t in words) / total
