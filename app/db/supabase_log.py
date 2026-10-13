"""Optional audit log to Supabase (tables in sql/app_tables.sql).

Runs as a FastAPI background task after the response is sent, so it adds no
latency, and it is skipped entirely when SUPABASE_URL / key are not set.
Uses the PostgREST endpoint directly; no Supabase SDK needed for two inserts.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)


async def insert(table: str, row: dict) -> None:
    if not settings.supabase_enabled:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(
                f"{settings.supabase_url}/rest/v1/{table}",
                json=row,
                headers={
                    "apikey": settings.supabase_key,
                    "Authorization": f"Bearer {settings.supabase_key}",
                    "Prefer": "return=minimal",
                },
            )
            if r.status_code >= 300:
                log.warning("Supabase insert into %s failed: %s %s", table, r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        log.warning("Supabase unreachable: %s %s", type(e).__name__, e)
