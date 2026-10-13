"""Load leads.csv into Supabase/Postgres in one command (alternative to the
Table Editor CSV import).

    python scripts/load_leads.py

Needs DATABASE_URL in .env (Supabase > Project Settings > Database > Connection
string > URI). Runs schema.sql, streams the CSV into lead_import with COPY,
then runs sql/transform_import.sql. Safe to re-run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def main() -> None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        sys.exit("DATABASE_URL is not set in .env")

    with psycopg.connect(url, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        cur.execute("truncate lead_import")
        with cur.copy("copy lead_import from stdin with (format csv, header true)") as copy:
            with open(ROOT / "leads.csv", "rb") as f:
                while chunk := f.read(1 << 16):
                    copy.write(chunk)
        cur.execute((ROOT / "sql" / "transform_import.sql").read_text(encoding="utf-8"))
        print("Loaded:", cur.fetchone())   # (rows_loaded, converted, b_suffixed)
        conn.commit()


if __name__ == "__main__":
    main()
