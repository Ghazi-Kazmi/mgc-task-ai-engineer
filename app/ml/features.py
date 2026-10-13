"""Data cleaning + feature definitions shared by training and live scoring.

Keeping these in one module guarantees the API scores a lead with exactly the
same transformations the model was trained with.

Column decisions (see README "Part 3" for the reasoning):
  USE (known when the lead arrives):
    source, city (normalised), area, property_type, budget_pkr_lac, bedrooms,
    is_overseas, referred_by_existing_client, has_financing_approved
  DROP - leakage:
    token_amount_received_pkr  a token is paid *as part of* converting; >0 for
                               every converted lead -> it is the label in disguise
  DROP - identifiers / no signal:
    lead_id, crm_record_hash   row identifiers
  DROP - only exists after we start working the lead (engagement):
    first_response_minutes, calls_made, total_call_seconds, whatsapp_replies,
    site_visits, agent_experience_years
    A brand-new lead has none of these yet, and the question is "who do we call
    first", so the production model can't depend on them. Trained separately
    for comparison only.
  created_at: used for the time split, not as a feature.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CITY_ALIASES = {"isb": "islamabad", "rwp": "rawalpindi", "khi": "karachi", "lhr": "lahore", "pew": "peshawar"}

CATEGORICAL = ["source", "city", "area", "property_type"]
NUMERIC = ["budget_pkr_lac", "bedrooms"]
BINARY = ["is_overseas", "referred_by_existing_client", "has_financing_approved"]
INTAKE_FEATURES = CATEGORICAL + NUMERIC + BINARY

ENGAGEMENT = ["first_response_minutes", "calls_made", "total_call_seconds", "whatsapp_replies",
              "site_visits", "agent_experience_years"]
LEAKAGE = ["token_amount_received_pkr"]
IDENTIFIERS = ["lead_id", "crm_record_hash"]
TARGET = "converted"


def normalize_city(value) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    key = str(value).strip().lower()
    return CITY_ALIASES.get(key, key).title()


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Same rule as queries.sql Q2: duplicates share crm_record_hash; keep the
    earliest created_at, then prefer the original id (no '-B' suffix), then lowest id."""
    ranked = df.assign(_is_copy=df["lead_id"].str.endswith("-B"))
    ranked = ranked.sort_values(["crm_record_hash", "created_at", "_is_copy", "lead_id"])
    return ranked.drop_duplicates("crm_record_hash", keep="first").drop(columns="_is_copy")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["city"] = df["city"].map(normalize_city)
    df["area"] = df["area"].fillna("Unknown")
    # bedrooms is missing *by design* for Plot / Commercial Shop -> 0 bedrooms, not "unknown".
    no_rooms = df["property_type"].isin(["Plot", "Commercial Shop"])
    df.loc[no_rooms, "bedrooms"] = df.loc[no_rooms, "bedrooms"].fillna(0)
    return df


def to_frame(lead: dict) -> pd.DataFrame:
    """Single lead from the web form -> one-row frame in training format."""
    row = {c: lead.get(c) for c in INTAKE_FEATURES}
    df = pd.DataFrame([row])
    df["city"] = df["city"].map(normalize_city)
    df["area"] = df["area"].fillna("Unknown")
    if row.get("property_type") in ("Plot", "Commercial Shop") and row.get("bedrooms") is None:
        df["bedrooms"] = 0
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in BINARY:
        df[c] = int(bool(row.get(c)))
    return df
