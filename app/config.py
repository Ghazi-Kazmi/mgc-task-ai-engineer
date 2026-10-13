"""Runtime settings, read once from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    docs_dir: Path = ROOT / "docs"
    leads_csv: Path = ROOT / "leads.csv"
    model_path: Path = ROOT / "models" / "lead_model.joblib"

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_project: str = os.getenv("OPENAI_PROJECT_ID", "").strip()
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    # Order to try LLM providers in; later ones are fallbacks.
    llm_providers: tuple[str, ...] = tuple(
        p.strip().lower() for p in os.getenv("LLM_PROVIDERS", "gemini,openai").split(",") if p.strip()
    )
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "15"))
    # The LLM critic adds one model call to document answers. Rule checks always run.
    llm_critic: bool = _bool("LLM_CRITIC", True)

    # Deployment
    cors_origins: tuple[str, ...] = tuple(
        o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
    )
    rate_limit_per_min: int = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))

    supabase_url: str = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    supabase_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key or self.openai_api_key)

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


settings = Settings()
