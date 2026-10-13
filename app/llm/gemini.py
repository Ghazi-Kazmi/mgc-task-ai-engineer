"""Gemini provider: validated JSON out, timeout, None on any failure."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

NAME = "gemini"
_client = None
# After a 429 (free tier: 5 requests/min) we skip Gemini until the window the
# API asked for has passed, instead of making every request wait on a call
# that will fail anyway. The next provider (or the no-LLM path) answers meanwhile.
_cooldown_until = 0.0


def available() -> bool:
    return bool(settings.gemini_api_key) and time.monotonic() >= _cooldown_until


def _get_client():
    global _client
    if _client is None:
        from google import genai  # imported lazily so the app runs without the key

        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def ping() -> None:
    """Free call (no generation quota) that opens the connection before the first question."""
    await _get_client().aio.models.get(model=settings.gemini_model)


async def generate(system: str, prompt: str, schema: type[T]) -> T | None:
    global _cooldown_until
    from google.genai import types

    config_kwargs = dict(
        system_instruction=system,
        temperature=0,
        response_mime_type="application/json",
        response_schema=schema,
    )
    # 2.5-series models "think" by default; for short grounded answers that is
    # mostly latency, so we turn it off. Other model families keep their default.
    if "2.5" in settings.gemini_model:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    try:
        resp = await asyncio.wait_for(
            _get_client().aio.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            ),
            timeout=settings.llm_timeout_s,
        )
        return schema.model_validate_json(resp.text or "")
    except (asyncio.TimeoutError, ValidationError) as e:
        log.warning("Gemini call failed (%s): %s", type(e).__name__, e)
    except Exception as e:  # network / quota / auth: degrade, never crash the request
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            wait = re.search(r"retry in ([\d.]+)s", msg)
            seconds = float(wait.group(1)) if wait else 30.0
            _cooldown_until = time.monotonic() + seconds
            log.warning("Gemini rate limit hit; skipping Gemini for %.0fs", seconds)
        else:
            log.warning("Gemini error: %s", msg[:300])
    return None
