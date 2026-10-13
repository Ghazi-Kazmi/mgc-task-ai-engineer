-- =============================================================================
-- MGC leads - schema (PostgreSQL / Supabase)
--
-- Run order (details in README "Supabase setup"):
--   1. this file                       -> creates lead_import (staging) + leads (clean)
--   2. import leads.csv into lead_import (Table Editor > Import CSV, or scripts/load_leads.py)
--   3. sql/transform_import.sql        -> casts + copies lead_import into leads
--   4. queries.sql                     -> the three analysis queries
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Staging table: the CRM dump exactly as exported. Everything is text so the
-- import never fails on a bad value; casting and validation happen in one
-- reviewed step (sql/transform_import.sql) instead of inside the importer.
-- -----------------------------------------------------------------------------
create table if not exists lead_import (
    lead_id                     text,
    created_at                  text,
    source                      text,
    city                        text,
    area                        text,
    property_type               text,
    budget_pkr_lac              text,
    bedrooms                    text,
    first_response_minutes      text,
    calls_made                  text,
    total_call_seconds          text,
    whatsapp_replies            text,
    site_visits                 text,
    agent_experience_years      text,
    is_overseas                 text,
    referred_by_existing_client text,
    has_financing_approved      text,
    token_amount_received_pkr   text,
    crm_record_hash             text,
    converted                   text
);


-- -----------------------------------------------------------------------------
-- leads: one row per lead, typed and constrained.
--
-- Why one table: every column describes the lead itself (1:1 with lead_id);
-- there are no repeating groups. source and property_type are small closed sets,
-- so CHECK constraints give the same protection as lookup tables at a fraction
-- of the complexity. If the business could add new sources from the CRM UI,
-- I'd move them to a lead_sources table with a FK.
--
-- What I would split out with a richer dump: agent_experience_years really
-- belongs to the *agent*, not the lead (a transitive dependency). With an
-- agent_id in the export it would become agents(agent_id, experience_years)
-- and leads.agent_id -> agents. The dump has no agent id, so it stays here.
--
-- city is stored as entered (ISB, Rwp, KARACHI, ...). The brief asks for the
-- normalization to happen in the query (queries.sql Q3). In production I'd add
-- a city_normalized column populated on insert from a city_aliases table.
-- -----------------------------------------------------------------------------
create table if not exists leads (
    lead_id                     text primary key,
    -- CRM exports local (PKT) wall-clock time without an offset; stored as an
    -- absolute instant so comparisons are unambiguous.
    created_at                  timestamptz not null,
    source                      text not null check (source in (
                                    'Facebook Ads', 'Property Portal', 'Google Search', 'Instagram',
                                    'Referral', 'Walk-in', 'WhatsApp Campaign', 'Expo Stall', 'Billboard')),
    city                        text not null,
    area                        text,                                  -- 5% missing in the dump
    property_type               text not null check (property_type in (
                                    'Apartment', 'Plot', 'Villa', 'Commercial Shop', 'Penthouse', 'Farmhouse')),
    budget_pkr_lac              numeric(10, 2) check (budget_pkr_lac > 0),
    -- NULL for Plot / Commercial Shop (not applicable), 1-5 otherwise
    bedrooms                    smallint check (bedrooms between 0 and 10),
    first_response_minutes      integer check (first_response_minutes >= 0),
    calls_made                  smallint not null default 0 check (calls_made >= 0),
    total_call_seconds          integer  not null default 0 check (total_call_seconds >= 0),
    whatsapp_replies            smallint not null default 0 check (whatsapp_replies >= 0),
    site_visits                 smallint not null default 0 check (site_visits >= 0),
    agent_experience_years      numeric(4, 1) check (agent_experience_years >= 0),
    is_overseas                 boolean not null,
    referred_by_existing_client boolean not null,
    has_financing_approved      boolean not null,
    token_amount_received_pkr   numeric(14, 2) not null default 0 check (token_amount_received_pkr >= 0),
    -- The CRM's fingerprint of the customer record. Duplicate entries share it
    -- (see queries.sql Q2). NOT unique yet because the dump contains duplicates;
    -- the unique constraint is added after the one-off cleanup in Q2.
    crm_record_hash             bigint not null,
    converted                   boolean not null,

    -- no call time without a call
    constraint calls_consistent check (calls_made > 0 or total_call_seconds = 0)
);

create index if not exists leads_created_at_idx on leads (created_at);       -- time-window reports, ML split
create index if not exists leads_source_idx     on leads (source);           -- Q1
create index if not exists leads_hash_idx       on leads (crm_record_hash);  -- Q2 dedupe lookups
