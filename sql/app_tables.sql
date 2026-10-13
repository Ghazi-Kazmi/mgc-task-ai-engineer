-- =============================================================================
-- Optional: audit tables the web app writes to (when SUPABASE_URL is set).
-- Every assistant answer is logged with its route, sources and critic verdict,
-- so wrong answers can be traced and the critic's failure rate monitored.
-- =============================================================================

create table if not exists assistant_logs (
    id             bigint generated always as identity primary key,
    created_at     timestamptz not null default now(),
    question       text not null,
    agent          text not null,
    route_method   text not null,
    status         text not null,
    answer         text not null,
    sources        jsonb not null default '[]'::jsonb,
    critic_passed  boolean not null,
    critic_checks  jsonb not null default '[]'::jsonb,
    used_llm       boolean not null,
    latency_ms     numeric(10, 1)
);

create table if not exists lead_scores (
    id           bigint generated always as identity primary key,
    created_at   timestamptz not null default now(),
    lead         jsonb not null,
    probability  numeric(6, 4) not null check (probability between 0 and 1),
    priority     text not null
);

create index if not exists assistant_logs_created_idx on assistant_logs (created_at desc);

-- Lock the tables down: RLS on with no policies means the public anon key can't
-- read or write them. The backend uses the service-role key, which bypasses RLS.
alter table assistant_logs enable row level security;
alter table lead_scores    enable row level security;
alter table lead_import    enable row level security;
alter table leads          enable row level security;
