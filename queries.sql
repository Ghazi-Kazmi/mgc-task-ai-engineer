-- =============================================================================
-- MGC leads - analysis queries (PostgreSQL / Supabase)
-- Run schema.sql + sql/transform_import.sql first.
--
-- All three queries count each real lead once. Q2 defines the rule for picking
-- the canonical row of a duplicate pair; Q1 and Q3 reuse the same rule as a CTE
-- so that duplicated leads don't inflate the conversion rates.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Conversion rate by lead source, best first, sources with 200+ leads only.
-- -----------------------------------------------------------------------------
with canonical as (
    select distinct on (crm_record_hash) *
    from leads
    order by crm_record_hash, created_at, (lead_id like '%-B'), lead_id
)
select
    source,
    count(*)                                         as leads,
    count(*) filter (where converted)                as conversions,
    round(100.0 * count(*) filter (where converted) / count(*), 2) as conversion_rate_pct
from canonical
group by source
having count(*) >= 200
order by conversion_rate_pct desc, leads desc;


-- -----------------------------------------------------------------------------
-- Q2. Duplicate leads: one canonical row per duplicate pair.
--
-- What a duplicate is: the same CRM record (same crm_record_hash) saved twice,
-- the second copy getting a "-B" suffixed lead_id. In the dump all 160 pairs
-- are identical apart from lead_id.
--   * Not created_at: two *different* leads share a timestamp
--     (MGC-100248 / MGC-103795, 2024-02-25 07:19:31), so time alone over-matches.
--   * Not the "-B" suffix alone: that's a naming accident of the CRM, not a key.
--     It is used only as a tie-breaker.
--
-- Deterministic winner, in order:
--   1. earliest created_at                     (first entry is the original)
--   2. lead_id without the '-B' suffix         (the original id, not the re-entry)
--   3. lowest lead_id                          (final tie-break, always unique)
--
-- Output: one row per duplicate pair = the canonical lead + the id it absorbs.
-- -----------------------------------------------------------------------------
with ranked as (
    select
        l.*,
        row_number() over w as rn,
        count(*)     over (partition by crm_record_hash) as copies
    from leads l
    window w as (partition by crm_record_hash
                 order by created_at, (lead_id like '%-B'), lead_id)
)
select
    keep.lead_id          as canonical_lead_id,
    dup.lead_id           as duplicate_lead_id,
    keep.crm_record_hash,
    keep.created_at,
    keep.source,
    keep.city,
    keep.property_type,
    keep.converted
from ranked keep
join ranked dup
  on dup.crm_record_hash = keep.crm_record_hash
 and dup.rn > 1
where keep.rn = 1
  and keep.copies > 1
order by keep.created_at;                       -- expect 160 rows

-- Preventing duplicates at the schema level:
--   * After the one-off cleanup below, make the CRM fingerprint unique so a
--     second agent cannot insert the same record:
--         alter table leads add constraint leads_crm_record_hash_key unique (crm_record_hash);
--   * The CRM / API then writes with an upsert instead of a blind insert, so a
--     second agent touching the lead updates it rather than cloning it:
--         insert into leads (...) values (...)
--         on conflict (crm_record_hash) do update set ...;
--   * Longer term the real natural key is the customer (normalised phone / CNIC),
--     which isn't in this dump; a unique index on that would also catch the same
--     person re-entered by hand with a different hash.
--
-- One-off cleanup (destructive, run deliberately):
--   delete from leads l
--   using (select lead_id,
--                 row_number() over (partition by crm_record_hash
--                                    order by created_at, (lead_id like '%-B'), lead_id) as rn
--          from leads) r
--   where l.lead_id = r.lead_id and r.rn > 1;
--   alter table leads add constraint leads_crm_record_hash_key unique (crm_record_hash);


-- -----------------------------------------------------------------------------
-- Q3. Conversion rate by city, with messy spellings normalised in the query.
--   'ISB' -> Islamabad, 'Rwp' -> Rawalpindi, 'khi' -> Karachi,
--   'LAHORE' / ' lahore ' -> Lahore   (trim + lower, map abbreviations, initcap)
-- -----------------------------------------------------------------------------
with canonical as (
    select distinct on (crm_record_hash) *
    from leads
    order by crm_record_hash, created_at, (lead_id like '%-B'), lead_id
),
normalized as (
    select
        case lower(trim(city))
            when 'isb' then 'Islamabad'
            when 'rwp' then 'Rawalpindi'
            when 'pindi' then 'Rawalpindi'
            when 'khi' then 'Karachi'
            when 'lhr' then 'Lahore'
            when 'pew' then 'Peshawar'
            else initcap(lower(trim(city)))
        end as city,
        converted
    from canonical
)
select
    city,
    count(*)                                         as leads,
    count(*) filter (where converted)                as conversions,
    round(100.0 * count(*) filter (where converted) / count(*), 2) as conversion_rate_pct
from normalized
group by city
order by conversion_rate_pct desc, leads desc;
