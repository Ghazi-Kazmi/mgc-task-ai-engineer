"""OpenAI provider: same contract as gemini.py (validated JSON, timeout, None on failure).

Uses Structured Outputs (`chat.completions.parse` with the Pydantic schema), so
the response is guaranteed to match the schema.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TypeVar

from pydantic import BaseModel

from ..config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

NAME = "openai"
_client = None
_cooldown_until = 0.0


def available() -> bool:
    return bool(settings.openai_api_key) and time.monotonic() >= _cooldown_until


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI  # imported lazily so the app runs without the key

        # One retry, not the SDK default of two with backoff: on a network blip we'd
        # rather fail over to the next provider quickly than burn the whole timeout.
        _client = AsyncOpenAI(api_key=settings.openai_api_key, project=settings.openai_project or None,
                              max_retries=1)
    return _client


async def ping() -> None:
    """Free call (no tokens) that opens the connection pool before the first question."""
    await _get_client().models.list()


async def generate(system: str, prompt: str, schema: type[T]) -> T | None:
    global _cooldown_until
    try:
        resp = await asyncio.wait_for(
            _get_client().chat.completions.parse(
                model=settings.openai_model,
                temperature=0,
                response_format=schema,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            ),
            timeout=settings.llm_timeout_s,
        )
        return resp.choices[0].message.parsed   # None if the model refused
    except asyncio.TimeoutError:
        log.warning("OpenAI call timed out after %ss", settings.llm_timeout_s)
    except Exception as e:  # network / quota / auth: degrade, never crash the request
        if getattr(e, "status_code", None) == 429:
            _cooldown_until = time.monotonic() + 30
            log.warning("OpenAI rate limit hit; skipping OpenAI for 30s")
        else:
            log.warning("OpenAI error: %s", str(e)[:300])
    return None
