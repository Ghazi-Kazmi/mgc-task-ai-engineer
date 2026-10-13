"""Request bodies for the API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class LeadRequest(BaseModel):
    source: str
    city: str
    area: Optional[str] = None
    property_type: str
    budget_pkr_lac: Optional[float] = Field(default=None, ge=0)
    bedrooms: Optional[int] = Field(default=None, ge=0, le=10)
    is_overseas: bool = False
    referred_by_existing_client: bool = False
    has_financing_approved: bool = False
