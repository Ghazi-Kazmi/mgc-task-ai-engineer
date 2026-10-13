"""FastAPI entry point.

    uvicorn app.main:app --reload

Everything heavy (documents, price book, conflict scan, model) is loaded once
at startup, so a request only does retrieval + at most two LLM calls.
The same process serves the frontend (frontend/) for local use; in production
the frontend can also be hosted separately (see documentation/DEPLOYMENT.md).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .agents.orchestrator import Orchestrator
from .config import ROOT, settings
from .db import supabase_log
from .knowledge.corpus import load_corpus
from .llm.client import active_providers, prewarm, warm_up
from .ml.scorer import LeadScorer
from .schemas import AskRequest, LeadRequest

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mgc")
FRONTEND = ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.assistant = Orchestrator(load_corpus(settings.docs_dir))
    if not settings.model_path.exists():
        log.info("No trained model found - training now (one-off, ~30s)...")
        from .ml.train import main as train
        train()
    app.state.scorer = LeadScorer(settings.model_path)
    warm_up()
    prewarm_task = asyncio.create_task(prewarm())   # don't block startup on the network
    log.info("Ready. LLM providers: %s | Supabase logging: %s | conflicts found in docs: %d",
             ", ".join(active_providers()) or "none (extractive fallback)",
             "on" if settings.supabase_enabled else "off",
             len(app.state.assistant.conflicts))
    yield
    prewarm_task.cancel()


app = FastAPI(title="MGC Sales Copilot", lifespan=lifespan)

if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


# --- simple per-IP rate limit -------------------------------------------------
# The public demo sits in front of paid LLM keys; this stops one visitor (or a
# script) from burning through them. In-memory is fine for a single instance.
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request) -> None:
    if settings.rate_limit_per_min <= 0:
        return
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.rate_limit_per_min:
        raise HTTPException(429, "Too many requests - please wait a minute and try again.")
    q.append(now)


# --- API ------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    a: Orchestrator = app.state.assistant
    return {
        "ok": True,
        "documents": len({s.doc_file for s in a.corpus.sections}),
        "sections": sum(1 for s in a.corpus.sections if s.heading != "Header"),
        "conflicts": [c.summary() for c in a.conflicts],
        "llm_providers": active_providers(),
        "audit_log": settings.supabase_enabled,
    }


@app.post("/api/ask")
async def ask(body: AskRequest, request: Request, background: BackgroundTasks) -> dict:
    rate_limit(request)
    result = await app.state.assistant.ask(body.question.strip())
    background.add_task(supabase_log.insert, "assistant_logs", {
        "question": result["question"],
        "agent": result["route"]["agent"],
        "route_method": result["route"]["method"],
        "status": result["status"],
        "answer": result["answer"],
        "sources": result["sources"],
        "critic_passed": result["critique"]["passed"],
        "critic_checks": result["critique"]["checks"],
        "used_llm": result["used_llm"],
        "latency_ms": result["timings_ms"]["total"],
    })
    return result


@app.get("/api/lead-options")
def lead_options() -> dict:
    s: LeadScorer = app.state.scorer
    return {"options": s.options, "model_report": s.report}


@app.post("/api/score")
async def score(body: LeadRequest, request: Request, background: BackgroundTasks) -> dict:
    rate_limit(request)
    s: LeadScorer = app.state.scorer
    lead = body.model_dump()
    for field in ("source", "property_type"):
        if lead[field] not in s.options[field]:
            raise HTTPException(422, f"Unknown {field} {lead[field]!r}; expected one of {s.options[field]}")
    result = s.score(lead)
    background.add_task(supabase_log.insert, "lead_scores", {
        "lead": lead, "probability": result["probability"], "priority": result["priority"],
    })
    return result


# --- frontend (mounted last so /api/* routes win) --------------------------------
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
