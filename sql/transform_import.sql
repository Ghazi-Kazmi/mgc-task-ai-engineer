-- =============================================================================
-- Copy the raw CSV rows from lead_import into the typed leads table.
-- Safe to re-run: existing lead_ids are skipped.
-- =============================================================================

insert into leads (
    lead_id, created_at, source, city, area, property_type, budget_pkr_lac, bedrooms,
    first_response_minutes, calls_made, total_call_seconds, whatsapp_replies, site_visits,
    agent_experience_years, is_overseas, referred_by_existing_client, has_financing_approved,
    token_amount_received_pkr, crm_record_hash, converted
)
select
    trim(lead_id),
    (trim(created_at)::timestamp at time zone 'Asia/Karachi'),
    trim(source),
    trim(city),
    nullif(trim(area), ''),
    trim(property_type),
    nullif(trim(budget_pkr_lac), '')::numeric,
    nullif(trim(bedrooms), '')::numeric::smallint,               -- "3.0" -> 3
    round(nullif(trim(first_response_minutes), '')::numeric)::integer,
    coalesce(nullif(trim(calls_made), ''), '0')::numeric::smallint,
    round(coalesce(nullif(trim(total_call_seconds), ''), '0')::numeric)::integer,
    coalesce(nullif(trim(whatsapp_replies), ''), '0')::numeric::smallint,
    coalesce(nullif(trim(site_visits), ''), '0')::numeric::smallint,
    nullif(trim(agent_experience_years), '')::numeric,
    trim(is_overseas) = '1',
    trim(referred_by_existing_client) = '1',
    trim(has_financing_approved) = '1',
    coalesce(nullif(trim(token_amount_received_pkr), ''), '0')::numeric,
    trim(crm_record_hash)::numeric::bigint,
    trim(converted) = '1'
from lead_import
on conflict (lead_id) do nothing;

-- Sanity check: expect 9160 rows, 634 converted, 160 '-B' duplicates.
select count(*)                                   as rows_loaded,
       count(*) filter (where converted)          as converted,
       count(*) filter (where lead_id like '%-B') as b_suffixed
from leads;
