"""The one entry point for every LLM call in the app.

Tries the configured providers in order (LLM_PROVIDERS, default "gemini,openai").
A provider is skipped if it has no key or is cooling down after a rate limit.
If every provider fails, it returns None and the caller uses its no-LLM path,
so a missing key, an outage or an exhausted quota never breaks a request.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

from pydantic import BaseModel

from ..config import settings
from . import gemini, openai_llm

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_PROVIDERS = {gemini.NAME: gemini, openai_llm.NAME: openai_llm}


async def generate_json(system: str, prompt: str, schema: type[T]) -> T | None:
    for name in settings.llm_providers:
        provider = _PROVIDERS.get(name)
        if provider is None or not provider.available():
            continue
        result = await provider.generate(system, prompt, schema)
        if result is not None:
            log.info("LLM call served by %s (%s)", name, schema.__name__)
            return result
        log.info("LLM provider %s failed, trying the next one", name)
    return None


def warm_up() -> None:
    """Import the SDKs and build the clients at startup (no API call, no quota),
    so the first user question doesn't pay several seconds of import time."""
    for name in settings.llm_providers:
        provider = _PROVIDERS.get(name)
        if provider is not None and provider.available():
            provider._get_client()


async def prewarm() -> None:
    """Background task at startup: resolve DNS / open TLS to each provider so the
    first real question doesn't pay for it. Failures are harmless and only logged."""
    for name in settings.llm_providers:
        provider = _PROVIDERS.get(name)
        if provider is None or not provider.available():
            continue
        try:
            await asyncio.wait_for(provider.ping(), timeout=20)
            log.info("LLM provider %s connection warmed", name)
        except Exception as e:
            log.warning("Could not pre-warm %s: %s", name, str(e)[:120])


def active_providers() -> list[str]:
    return [n for n in settings.llm_providers if n in _PROVIDERS and _PROVIDERS[n].available()]
