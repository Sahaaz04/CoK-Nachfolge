-- ============================================================
-- SUCCESSION ANALYSIS DATABASE SCHEMA
-- OpenRegister-first architecture
--
-- Main rule:
-- One company appears only once in companies.
-- openregister_company_id is the global unique key.
-- ============================================================

create extension if not exists "pgcrypto";


-- ============================================================
-- 1. SEARCH RUNS
-- Stores every OpenRegister filter-search execution.
-- Useful for audit, debugging, and knowing which filters produced which companies.
-- ============================================================

create table if not exists openregister_search_runs (
    id uuid primary key default gen_random_uuid(),

    search_name text,
    filters_json jsonb,
    query_json jsonb,
    pagination_json jsonb,

    requested_max_companies integer,
    returned_companies integer default 0,
    saved_companies integer default 0,
    skipped_existing_companies integer default 0,

    api_status text,
    error_message text,

    created_at timestamptz default now()
);


create index if not exists openregister_search_runs_created_at_idx
on openregister_search_runs(created_at);


-- ============================================================
-- 2. MASTER COMPANIES
-- One row per OpenRegister company.
-- Do NOT duplicate companies across searches.
-- ============================================================

create table if not exists companies (
    id uuid primary key default gen_random_uuid(),

    -- Main OpenRegister identity.
    openregister_company_id text not null unique,

    -- Keep register_id for compatibility with previous app logic.
    -- For OpenRegister companies, this should usually equal openregister_company_id.
    register_id text not null unique,

    -- Search/result fields.
    name text,
    legal_form text,
    active boolean,
    country text,
    register_number text,
    register_court text,
    register_type text,

    -- Useful normalized company-detail fields.
    status text,
    city text,
    postal_code text,
    street text,
    formatted_address text,
    website text,
    email text,
    phone text,
    vat_id text,
    lei text,

    purpose text,
    industry_codes jsonb,

    -- Latest financial indicator fields from company details / indicators.
    financials_date text,
    revenue_eur numeric,
    employees numeric,
    balance_sheet_total_eur numeric,
    net_income_eur numeric,
    equity_eur numeric,
    cash_eur numeric,
    liabilities_eur numeric,
    real_estate_eur numeric,
    capital_amount_eur numeric,

    -- Ownership summary fields derived from owners endpoint/search filters.
    number_of_owners integer,
    natural_person_owner_count integer,
    legal_person_owner_count integer,
    youngest_owner_age integer,
    oldest_owner_age integer,
    has_sole_owner boolean,
    has_representative_owner boolean,
    is_family_owned boolean,
    has_majority_owner boolean,
    largest_owner_percentage numeric,

    -- Pipeline status.
    source text default 'openregister_search',
    last_search_run_id uuid references openregister_search_runs(id) on delete set null,

    company_info_enriched_at timestamptz,
    financials_enriched_at timestamptz,
    ownership_enriched_at timestamptz,
    ubos_enriched_at timestamptz,

    raw_search_result jsonb,
    raw_company_details jsonb,

    created_at timestamptz default now(),
    updated_at timestamptz default now()
);


create index if not exists companies_openregister_company_id_idx
on companies(openregister_company_id);

create index if not exists companies_register_id_idx
on companies(register_id);

create index if not exists companies_name_idx
on companies(name);

create index if not exists companies_legal_form_idx
on companies(legal_form);

create index if not exists companies_active_idx
on companies(active);

create index if not exists companies_last_search_run_id_idx
on companies(last_search_run_id);


-- ============================================================
-- 3. COMPANY FINANCIALS
-- One row per company for raw/merged financials.
-- Keep raw JSON first because OpenRegister financial reports are nested.
-- ============================================================

create table if not exists company_financials (
    id uuid primary key default gen_random_uuid(),

    company_register_id text not null references companies(register_id) on delete cascade,
    openregister_company_id text not null references companies(openregister_company_id) on delete cascade,
    company_name text,

    report_count integer,
    latest_report_start_date text,
    latest_report_end_date text,

    raw_financials jsonb,

    api_status text,
    notes text,

    enriched_at timestamptz default now(),
    updated_at timestamptz default now(),

    unique(openregister_company_id)
);


create index if not exists company_financials_company_register_id_idx
on company_financials(company_register_id);

create index if not exists company_financials_openregister_company_id_idx
on company_financials(openregister_company_id);


-- ============================================================
-- 4. SHAREHOLDERS / OWNERS
-- Multiple rows per company.
-- OpenRegister owner id may be null, so dedupe uses a stable generated owner_key.
-- ============================================================

create table if not exists shareholders (
    id uuid primary key default gen_random_uuid(),

    company_register_id text not null references companies(register_id) on delete cascade,
    openregister_company_id text not null references companies(openregister_company_id) on delete cascade,
    company_name text,

    owner_key text not null,
    owner_id text,
    owner_type text,          -- natural_person / legal_person
    relation_type text,       -- shareholder / stockholder / limited_partner / general_partner

    shareholder_name text,

    natural_person_full_name text,
    natural_person_first_name text,
    natural_person_last_name text,
    date_of_birth text,
    age integer,

    legal_person_name text,

    owner_city text,
    owner_country text,

    nominal_share_eur numeric,
    percentage_share numeric,
    relation_start_date text,

    best_available boolean,
    sources_json jsonb,

    api_status text,
    notes text,

    retrieved_at timestamptz default now(),
    updated_at timestamptz default now(),

    raw_data jsonb,

    unique(openregister_company_id, owner_key)
);


create index if not exists shareholders_company_register_id_idx
on shareholders(company_register_id);

create index if not exists shareholders_openregister_company_id_idx
on shareholders(openregister_company_id);

create index if not exists shareholders_name_idx
on shareholders(shareholder_name);

create index if not exists shareholders_owner_type_idx
on shareholders(owner_type);

create index if not exists shareholders_relation_type_idx
on shareholders(relation_type);


-- ============================================================
-- 5. COMPANY UBOS
-- Multiple rows per company.
-- Optional enrichment.
-- ============================================================

create table if not exists company_ubos (
    id uuid primary key default gen_random_uuid(),

    company_register_id text not null references companies(register_id) on delete cascade,
    openregister_company_id text not null references companies(openregister_company_id) on delete cascade,
    company_name text,

    ubo_key text not null,
    ubo_id text,
    ubo_name text,

    ubo_type text,
    percentage_share numeric,
    max_percentage_share numeric,

    natural_person_full_name text,
    natural_person_first_name text,
    natural_person_last_name text,
    date_of_birth text,
    age integer,

    legal_person_name text,
    ubo_city text,
    ubo_country text,

    api_status text,
    notes text,

    enriched_at timestamptz default now(),
    updated_at timestamptz default now(),

    raw_data jsonb,

    unique(openregister_company_id, ubo_key)
);


create index if not exists company_ubos_company_register_id_idx
on company_ubos(company_register_id);

create index if not exists company_ubos_openregister_company_id_idx
on company_ubos(openregister_company_id);

create index if not exists company_ubos_name_idx
on company_ubos(ubo_name);


-- ============================================================
-- 6. COMPANY MODELS
-- Claude business model summaries.
-- Kept from previous app, but linked to OpenRegister company.
-- ============================================================

create table if not exists company_models (
    id uuid primary key default gen_random_uuid(),

    company_register_id text not null references companies(register_id) on delete cascade,
    openregister_company_id text references companies(openregister_company_id) on delete cascade,

    company_name text,
    website text,

    model_provider text not null default 'claude',
    model_name text not null,

    business_segment text,
    summary text,

    api_status text,
    notes text,

    raw_data jsonb,

    created_at timestamptz default now(),
    updated_at timestamptz default now(),

    unique(company_register_id, model_provider)
);


create index if not exists company_models_company_register_id_idx
on company_models(company_register_id);

create index if not exists company_models_openregister_company_id_idx
on company_models(openregister_company_id);

create index if not exists company_models_provider_idx
on company_models(model_provider);


-- ============================================================
-- 7. FIT SCORES
-- Kept from previous app.
-- One score row per company + provider.
-- ============================================================

create table if not exists company_fit_scores (
    id uuid primary key default gen_random_uuid(),

    company_register_id text not null references companies(register_id) on delete cascade,
    openregister_company_id text references companies(openregister_company_id) on delete cascade,

    company_name text,

    fit_score integer,
    fit_label text,
    fit_comment text,

    succession_signal text,
    financial_signal text,
    shareholder_signal text,
    risk_flags text,
    recommended_action text,

    model_provider text default 'claude',
    model_name text,

    scoring_config jsonb,

    api_status text,
    notes text,
    raw_data jsonb,

    created_at timestamptz default now(),
    updated_at timestamptz default now(),

    unique(company_register_id, model_provider)
);


create index if not exists company_fit_scores_company_register_id_idx
on company_fit_scores(company_register_id);

create index if not exists company_fit_scores_openregister_company_id_idx
on company_fit_scores(openregister_company_id);

create index if not exists company_fit_scores_score_idx
on company_fit_scores(fit_score);


-- ============================================================
-- 8. PROCESSING / ENRICHMENT LOGS
-- For every search/enrichment event.
-- ============================================================

create table if not exists processing_logs (
    id uuid primary key default gen_random_uuid(),

    company_register_id text,
    openregister_company_id text,
    company_name text,

    search_run_id uuid references openregister_search_runs(id) on delete set null,

    module text,
    endpoint text,
    status text,
    message text,
    error_message text,

    raw_data jsonb,

    created_at timestamptz default now()
);


create index if not exists processing_logs_company_register_id_idx
on processing_logs(company_register_id);

create index if not exists processing_logs_openregister_company_id_idx
on processing_logs(openregister_company_id);

create index if not exists processing_logs_module_idx
on processing_logs(module);

create index if not exists processing_logs_status_idx
on processing_logs(status);

create index if not exists processing_logs_created_at_idx
on processing_logs(created_at);


-- ============================================================
-- 9. UPDATED_AT TRIGGER
-- Keeps updated_at fresh on updates.
-- ============================================================

create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;


drop trigger if exists set_companies_updated_at on companies;
create trigger set_companies_updated_at
before update on companies
for each row execute function set_updated_at();

drop trigger if exists set_company_financials_updated_at on company_financials;
create trigger set_company_financials_updated_at
before update on company_financials
for each row execute function set_updated_at();

drop trigger if exists set_shareholders_updated_at on shareholders;
create trigger set_shareholders_updated_at
before update on shareholders
for each row execute function set_updated_at();

drop trigger if exists set_company_ubos_updated_at on company_ubos;
create trigger set_company_ubos_updated_at
before update on company_ubos
for each row execute function set_updated_at();

drop trigger if exists set_company_models_updated_at on company_models;
create trigger set_company_models_updated_at
before update on company_models
for each row execute function set_updated_at();

drop trigger if exists set_company_fit_scores_updated_at on company_fit_scores;
create trigger set_company_fit_scores_updated_at
before update on company_fit_scores
for each row execute function set_updated_at();


-- ============================================================
-- 10. MASTER OVERVIEW VIEW
-- One row per company for Streamlit + Google Sheets.
-- ============================================================

drop view if exists master_overview;

create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,

    c.city,
    c.postal_code,
    c.website,
    c.email,
    c.phone,
    c.lei,

    c.purpose,
    c.industry_codes,

    c.revenue_eur,
    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,

    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,

    (
        select sh.shareholder_name
        from shareholders sh
        where sh.openregister_company_id = c.openregister_company_id
          and coalesce(sh.shareholder_name, '') <> ''
        order by sh.percentage_share desc nulls last, sh.retrieved_at desc
        limit 1
    ) as main_owner_name,

    (
        select sh.owner_type
        from shareholders sh
        where sh.openregister_company_id = c.openregister_company_id
          and coalesce(sh.shareholder_name, '') <> ''
        order by sh.percentage_share desc nulls last, sh.retrieved_at desc
        limit 1
    ) as main_owner_type,

    (
        select sh.percentage_share
        from shareholders sh
        where sh.openregister_company_id = c.openregister_company_id
          and coalesce(sh.shareholder_name, '') <> ''
        order by sh.percentage_share desc nulls last, sh.retrieved_at desc
        limit 1
    ) as main_owner_percentage_share,

    cf.report_count,
    cf.latest_report_start_date,
    cf.latest_report_end_date,

    cm.business_segment as claude_business_segment,
    cm.summary as detailed_business_model,

    fs.fit_score,
    fs.fit_label,
    fs.fit_comment,
    fs.succession_signal,
    fs.financial_signal,
    fs.shareholder_signal,
    fs.risk_flags,
    fs.recommended_action,

    c.company_info_enriched_at,
    c.financials_enriched_at,
    c.ownership_enriched_at,
    c.ubos_enriched_at,

    c.created_at,
    c.updated_at,

    greatest(
        c.updated_at,
        coalesce(cf.updated_at, c.updated_at),
        coalesce(cm.updated_at, c.updated_at),
        coalesce(fs.updated_at, c.updated_at)
    ) as last_updated_at

from companies c

left join company_financials cf
    on cf.openregister_company_id = c.openregister_company_id

left join lateral (
    select *
    from company_models cm
    where cm.openregister_company_id = c.openregister_company_id
       or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true

left join lateral (
    select *
    from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id
       or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;


-- ============================================================
-- 11. RLS LOCKDOWN
-- Service role key can still access these from Streamlit backend.
-- ============================================================

alter table if exists openregister_search_runs enable row level security;
alter table if exists companies enable row level security;
alter table if exists company_financials enable row level security;
alter table if exists shareholders enable row level security;
alter table if exists company_ubos enable row level security;
alter table if exists company_models enable row level security;
alter table if exists company_fit_scores enable row level security;
alter table if exists processing_logs enable row level security;

revoke all on table openregister_search_runs from anon, authenticated;
revoke all on table companies from anon, authenticated;
revoke all on table company_financials from anon, authenticated;
revoke all on table shareholders from anon, authenticated;
revoke all on table company_ubos from anon, authenticated;
revoke all on table company_models from anon, authenticated;
revoke all on table company_fit_scores from anon, authenticated;
revoke all on table processing_logs from anon, authenticated;
revoke all on table master_overview from anon, authenticated;

-- ADDITION --

drop view if exists master_overview;
create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,
    c.city,
    c.postal_code,
    c.website,
    c.email,
    c.phone,
    c.lei,
    c.purpose,
    c.industry_codes,
    c.revenue_eur,
    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,
    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,
    main_owner.shareholder_name as main_owner_name,
    main_owner.owner_type as main_owner_type,
    main_owner.percentage_share as main_owner_percentage_share,
    main_ubo.ubo_name as main_ubo_name,
    main_ubo.age as main_ubo_age,
    main_ubo.percentage_share as main_ubo_percentage_share,
    main_ubo.max_percentage_share as main_ubo_max_percentage_share,
    cm.business_segment as claude_business_segment,
    fs.fit_score,
    fs.fit_label,
    fs.fit_comment,
    fs.recommended_action
from companies c
left join lateral (
    select sh.shareholder_name, sh.owner_type, sh.percentage_share
    from shareholders sh
    where sh.openregister_company_id = c.openregister_company_id
    order by sh.percentage_share desc nulls last, sh.retrieved_at desc
    limit 1
) main_owner on true
left join lateral (
    select u.ubo_name, u.age, u.percentage_share, u.max_percentage_share
    from company_ubos u
    where u.openregister_company_id = c.openregister_company_id
    order by coalesce(u.percentage_share, u.max_percentage_share) desc nulls last, u.enriched_at desc
    limit 1
) main_ubo on true
left join lateral (
    select * from company_models cm
    where cm.openregister_company_id = c.openregister_company_id or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true
left join lateral (
    select * from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;

-- ADDITION --
-- v0.7: remove LEI from overview and expose detailed Claude business segment.
drop view if exists master_overview;
create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,
    c.city,
    c.postal_code,
    c.website,
    c.email,
    c.phone,
    c.purpose,
    c.industry_codes,
    c.revenue_eur,
    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,
    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,
    main_owner.shareholder_name as main_owner_name,
    main_owner.owner_type as main_owner_type,
    main_owner.percentage_share as main_owner_percentage_share,
    main_ubo.ubo_name as main_ubo_name,
    main_ubo.age as main_ubo_age,
    main_ubo.percentage_share as main_ubo_percentage_share,
    main_ubo.max_percentage_share as main_ubo_max_percentage_share,
    cm.business_segment as claude_business_segment,
    cm.summary as claude_detailed_business_segment,
    fs.fit_score,
    fs.fit_label,
    fs.fit_comment,
    fs.recommended_action
from companies c
left join lateral (
    select sh.shareholder_name, sh.owner_type, sh.percentage_share
    from shareholders sh
    where sh.openregister_company_id = c.openregister_company_id
    order by sh.percentage_share desc nulls last, sh.retrieved_at desc
    limit 1
) main_owner on true
left join lateral (
    select u.ubo_name, u.age, u.percentage_share, u.max_percentage_share
    from company_ubos u
    where u.openregister_company_id = c.openregister_company_id
    order by coalesce(u.percentage_share, u.max_percentage_share) desc nulls last, u.enriched_at desc
    limit 1
) main_ubo on true
left join lateral (
    select * from company_models cm
    where cm.openregister_company_id = c.openregister_company_id or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true
left join lateral (
    select * from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;

--ADDITION--

-- v0.8: remove Recommended Action from master_overview display/view.
drop view if exists master_overview;
create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,
    c.city,
    c.postal_code,
    c.website,
    c.email,
    c.phone,
    c.purpose,
    c.industry_codes,
    c.revenue_eur,
    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,
    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,
    main_owner.shareholder_name as main_owner_name,
    main_owner.owner_type as main_owner_type,
    main_owner.percentage_share as main_owner_percentage_share,
    main_ubo.ubo_name as main_ubo_name,
    main_ubo.age as main_ubo_age,
    main_ubo.percentage_share as main_ubo_percentage_share,
    main_ubo.max_percentage_share as main_ubo_max_percentage_share,
    cm.business_segment as claude_business_segment,
    cm.summary as claude_detailed_business_segment,
    fs.fit_score,
    fs.fit_label,
    fs.fit_comment
from companies c
left join lateral (
    select sh.shareholder_name, sh.owner_type, sh.percentage_share
    from shareholders sh
    where sh.openregister_company_id = c.openregister_company_id
    order by sh.percentage_share desc nulls last, sh.retrieved_at desc
    limit 1
) main_owner on true
left join lateral (
    select u.ubo_name, u.age, u.percentage_share, u.max_percentage_share
    from company_ubos u
    where u.openregister_company_id = c.openregister_company_id
    order by coalesce(u.percentage_share, u.max_percentage_share) desc nulls last, u.enriched_at desc
    limit 1
) main_ubo on true
left join lateral (
    select * from company_models cm
    where cm.openregister_company_id = c.openregister_company_id or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true
left join lateral (
    select * from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;

-- ADDITION --
drop view if exists master_overview;

create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,
    c.city,
    c.postal_code,
    c.website,
    c.email,
    c.phone,
    c.purpose,
    c.industry_codes,
    c.revenue_eur,
    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,
    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,
    cm.business_segment as claude_business_segment,
    cm.summary as claude_detailed_business_segment,
    fs.fit_score,
    fs.fit_label,
    fs.fit_comment
from companies c
left join lateral (
    select *
    from company_models cm
    where cm.openregister_company_id = c.openregister_company_id
       or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true
left join lateral (
    select *
    from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id
       or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;

-- Addition --

begin;

-- ============================================================
-- NORTHDATA INTEGRATION BASELINE
-- Final rule:
-- - No temporary NorthData company ID
-- - No nullable final IDs
-- - NorthData rows must be matched to OpenRegister before insert/update
-- - openregister_company_id and register_id remain the final unique IDs
-- ============================================================

-- Remove the earlier unnecessary duplicate-protection idea if it exists.
drop index if exists companies_register_identity_unique_idx;
drop function if exists normalize_register_text(text);

-- Stop if any company currently has missing final IDs.
-- This prevents silently damaging the existing company identity logic.
do $$
begin
    if exists (
        select 1
        from companies
        where openregister_company_id is null
           or trim(openregister_company_id) = ''
           or register_id is null
           or trim(register_id) = ''
    ) then
        raise exception 'There are companies with missing openregister_company_id/register_id. Fix or remove those rows before enforcing final OpenRegister IDs.';
    end if;
end $$;

-- Keep OpenRegister ID required.
alter table companies
    alter column openregister_company_id set not null;

alter table companies
    alter column register_id set not null;

-- Keep existing unique identity constraints safe.
-- These constraints usually already exist from the original schema.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'companies_openregister_company_id_key'
    ) then
        alter table companies
            add constraint companies_openregister_company_id_key unique (openregister_company_id);
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'companies_register_id_key'
    ) then
        alter table companies
            add constraint companies_register_id_key unique (register_id);
    end if;
end $$;

-- Clean final Overview view.
-- No NorthData debug columns.
-- No temporary IDs.
-- No LEI.
-- No Recommended Action.
drop view if exists master_overview;

create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,
    c.city,
    c.postal_code,
    c.website,
    c.email,
    c.phone,
    c.purpose,
    c.industry_codes,

    c.revenue_eur,
    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,

    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,

    cm.business_segment as claude_business_segment,
    cm.summary as claude_detailed_business_segment,

    fs.fit_score,
    fs.fit_label,
    fs.fit_comment

from companies c

left join lateral (
    select *
    from company_models cm
    where cm.openregister_company_id = c.openregister_company_id
       or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true

left join lateral (
    select *
    from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id
       or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;

revoke all on table master_overview from anon, authenticated;

commit;

-- Addition --

begin;

-- ============================================================
-- v0.9 SQL BASELINE
-- Source-separated revenue/WZ + shareholder year + Claude model column
--
-- Important:
-- - No SQL splitting of "Cosmetics - machinery..."
-- - Claude prompt/code will fill business_segment and business_model properly
-- ============================================================

drop view if exists master_overview;

-- 1. Separate revenue by source.
alter table companies
    add column if not exists openregister_revenue_eur numeric;

alter table companies
    add column if not exists northdata_revenue_eur numeric;

-- 2. Separate WZ / industry source columns.
alter table companies
    add column if not exists openregister_wz_codes jsonb;

alter table companies
    add column if not exists northdata_wz_code text;

-- Backfill OpenRegister WZ from existing OpenRegister industry_codes only.
-- This is not fallback; this only preserves old OpenRegister data.
update companies
set openregister_wz_codes = industry_codes
where openregister_wz_codes is null
  and industry_codes is not null
  and coalesce(source, '') <> 'northdata_import';

-- Backfill revenue into source-specific columns only when source is clear.
-- This does not mix NorthData and OpenRegister.
update companies
set openregister_revenue_eur = revenue_eur
where openregister_revenue_eur is null
  and revenue_eur is not null
  and coalesce(source, '') <> 'northdata_import';

update companies
set northdata_revenue_eur = revenue_eur
where northdata_revenue_eur is null
  and revenue_eur is not null
  and coalesce(source, '') = 'northdata_import';

-- 3. Shareholder integrated year.
alter table shareholders
    add column if not exists relation_start_year integer;

update shareholders
set relation_start_year =
    case
        when substring(relation_start_date from '([12][0-9]{3})') is not null
        then substring(relation_start_date from '([12][0-9]{3})')::integer
        else null
    end
where relation_start_year is null
  and relation_start_date is not null;

-- 4. Claude business model column.
-- Do NOT split old business_segment by hyphen.
-- New Claude prompt/code will fill this column correctly.
alter table company_models
    add column if not exists business_model text;

-- ============================================================
-- 5. Rebuild master_overview
-- - Phone removed
-- - Revenue separated by source
-- - WZ separated by source
-- - Year integrated added after owner %
-- - Claude segment/model separated
-- ============================================================

create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,

    c.city,
    c.postal_code,
    c.website,
    c.email,

    c.purpose,

    c.openregister_wz_codes,
    c.northdata_wz_code,

    c.openregister_revenue_eur,
    c.northdata_revenue_eur,

    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,

    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,

    main_owner.shareholder_name as main_owner_name,
    main_owner.owner_type as main_owner_type,
    main_owner.percentage_share as main_owner_percentage_share,
    main_owner.relation_start_year as main_owner_year_integrated,

    main_ubo.ubo_name as main_ubo_name,
    main_ubo.age as main_ubo_age,
    main_ubo.percentage_share as main_ubo_percentage_share,
    main_ubo.max_percentage_share as main_ubo_max_percentage_share,

    cm.business_segment as claude_business_segment,
    cm.business_model as claude_business_model,
    cm.summary as claude_detailed_business_summary,

    fs.fit_score,
    fs.fit_label,
    fs.fit_comment

from companies c

left join lateral (
    select
        sh.shareholder_name,
        sh.owner_type,
        sh.percentage_share,
        sh.relation_start_year
    from shareholders sh
    where sh.openregister_company_id = c.openregister_company_id
      and coalesce(sh.shareholder_name, '') <> ''
    order by sh.percentage_share desc nulls last, sh.retrieved_at desc
    limit 1
) main_owner on true

left join lateral (
    select
        u.ubo_name,
        u.age,
        u.percentage_share,
        u.max_percentage_share
    from company_ubos u
    where u.openregister_company_id = c.openregister_company_id
    order by coalesce(u.percentage_share, u.max_percentage_share) desc nulls last, u.enriched_at desc
    limit 1
) main_ubo on true

left join lateral (
    select *
    from company_models cm
    where cm.openregister_company_id = c.openregister_company_id
       or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true

left join lateral (
    select *
    from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id
       or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;

revoke all on table master_overview from anon, authenticated;

commit;

-- Addition --
begin;

drop view if exists master_overview;

alter table companies
    add column if not exists founding_year integer;

update companies
set founding_year =
    substring(raw_company_details->>'incorporated_at' from '([12][0-9]{3})')::integer
where founding_year is null
  and raw_company_details is not null
  and substring(raw_company_details->>'incorporated_at' from '([12][0-9]{3})') is not null;

alter table shareholders
    drop column if exists relation_start_year;

alter table shareholders
    drop column if exists relation_start_date;

create view master_overview as
select
    c.register_id,
    c.openregister_company_id,
    c.name as company_name,
    c.legal_form,
    c.founding_year,
    c.active,
    c.country,
    c.register_number,
    c.register_court,
    c.register_type,

    c.city,
    c.postal_code,
    c.website,
    c.email,

    c.purpose,

    c.openregister_wz_codes,
    c.northdata_wz_code,

    c.openregister_revenue_eur,
    c.northdata_revenue_eur,

    c.employees,
    c.balance_sheet_total_eur,
    c.net_income_eur,
    c.equity_eur,
    c.cash_eur,
    c.liabilities_eur,
    c.real_estate_eur,
    c.capital_amount_eur,
    c.financials_date,

    c.number_of_owners,
    c.natural_person_owner_count,
    c.legal_person_owner_count,
    c.youngest_owner_age,
    c.oldest_owner_age,
    c.has_sole_owner,
    c.has_representative_owner,
    c.is_family_owned,
    c.has_majority_owner,
    c.largest_owner_percentage,

    main_owner.shareholder_name as main_owner_name,
    main_owner.owner_type as main_owner_type,
    main_owner.percentage_share as main_owner_percentage_share,

    main_ubo.ubo_name as main_ubo_name,
    main_ubo.age as main_ubo_age,
    main_ubo.percentage_share as main_ubo_percentage_share,
    main_ubo.max_percentage_share as main_ubo_max_percentage_share,

    cm.business_segment as claude_business_segment,
    cm.business_model as claude_business_model,
    cm.summary as claude_detailed_business_summary,

    fs.fit_score,
    fs.fit_label,
    fs.fit_comment

from companies c

left join lateral (
    select
        sh.shareholder_name,
        sh.owner_type,
        sh.percentage_share
    from shareholders sh
    where sh.openregister_company_id = c.openregister_company_id
      and coalesce(sh.shareholder_name, '') <> ''
    order by sh.percentage_share desc nulls last, sh.retrieved_at desc
    limit 1
) main_owner on true

left join lateral (
    select
        u.ubo_name,
        u.age,
        u.percentage_share,
        u.max_percentage_share
    from company_ubos u
    where u.openregister_company_id = c.openregister_company_id
    order by coalesce(u.percentage_share, u.max_percentage_share) desc nulls last, u.enriched_at desc
    limit 1
) main_ubo on true

left join lateral (
    select *
    from company_models cm
    where cm.openregister_company_id = c.openregister_company_id
       or cm.company_register_id = c.register_id
    order by coalesce(cm.updated_at, cm.created_at) desc nulls last
    limit 1
) cm on true

left join lateral (
    select *
    from company_fit_scores fs
    where fs.openregister_company_id = c.openregister_company_id
       or fs.company_register_id = c.register_id
    order by coalesce(fs.updated_at, fs.created_at) desc nulls last
    limit 1
) fs on true;

grant select on table master_overview to anon, authenticated;

notify pgrst, 'reload schema';

commit;
