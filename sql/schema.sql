-- ============================================================
-- SUCCESSION ANALYSIS DATABASE SCHEMA
-- OpenRegister base + NorthData integration
--
-- Main rule:
-- - OpenRegister remains the base identity for companies in main tables.
-- - NorthData uploads are stored separately first.
-- - Only NorthData rows matched to OpenRegister enter companies.
-- ============================================================

create extension if not exists "pgcrypto";

drop view if exists master_overview;


-- ============================================================
-- 1. SEARCH RUNS
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
-- 2. NORTHDATA IMPORTS
-- Uploaded NorthData files + row-level OpenRegister matching.
-- ============================================================

create table if not exists northdata_import_batches (
    id uuid primary key default gen_random_uuid(),

    file_name text,
    row_count integer default 0,
    matched_count integer default 0,
    manual_review_count integer default 0,
    unmatched_count integer default 0,
    error_count integer default 0,

    column_mapping_json jsonb,

    status text,
    notes text,

    created_at timestamptz default now(),
    finished_at timestamptz
);

create index if not exists northdata_import_batches_created_at_idx
on northdata_import_batches(created_at);


create table if not exists northdata_import_rows (
    id uuid primary key default gen_random_uuid(),

    batch_id uuid references northdata_import_batches(id) on delete cascade,
    row_number integer,

    company_name text,
    register_court text,
    register_id text,
    register_type text,
    register_number text,
    legal_form text,
    city text,
    postal_code text,
    country_code text,
    website text,

    financials_date text,
    revenue_eur numeric,
    employees numeric,
    balance_sheet_total_eur numeric,
    net_income_eur numeric,
    equity_eur numeric,
    cash_eur numeric,
    liabilities_eur numeric,

    raw_json jsonb,
    normalized_json jsonb,

    openregister_company_id text,
    match_status text default 'pending',
    match_confidence numeric,
    match_method text,
    match_notes text,
    candidate_json jsonb,

    imported_to_companies_at timestamptz,

    created_at timestamptz default now()
);

create index if not exists northdata_import_rows_batch_id_idx
on northdata_import_rows(batch_id);

create index if not exists northdata_import_rows_match_status_idx
on northdata_import_rows(match_status);

create index if not exists northdata_import_rows_openregister_company_id_idx
on northdata_import_rows(openregister_company_id);


-- ============================================================
-- 3. MASTER COMPANIES
-- One row per OpenRegister company.
-- ============================================================

create table if not exists companies (
    id uuid primary key default gen_random_uuid(),

    openregister_company_id text not null unique,
    register_id text not null unique,

    name text,
    legal_form text,
    active boolean,
    country text,
    register_number text,
    register_court text,
    register_type text,

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

    source text default 'openregister_search',
    company_data_source text default 'openregister',
    financial_data_source text default 'openregister',

    northdata_import_batch_id uuid references northdata_import_batches(id) on delete set null,
    northdata_import_row_id uuid references northdata_import_rows(id) on delete set null,
    northdata_raw_data jsonb,
    northdata_match_status text,
    northdata_match_confidence numeric,
    northdata_match_method text,

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

-- Existing DB safety: add NorthData columns if companies table already existed.
alter table companies
add column if not exists company_data_source text default 'openregister',
add column if not exists financial_data_source text default 'openregister',
add column if not exists northdata_import_batch_id uuid references northdata_import_batches(id) on delete set null,
add column if not exists northdata_import_row_id uuid references northdata_import_rows(id) on delete set null,
add column if not exists northdata_raw_data jsonb,
add column if not exists northdata_match_status text,
add column if not exists northdata_match_confidence numeric,
add column if not exists northdata_match_method text;

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

create index if not exists companies_northdata_import_batch_id_idx
on companies(northdata_import_batch_id);

create index if not exists companies_northdata_match_status_idx
on companies(northdata_match_status);


-- ============================================================
-- 4. COMPANY FINANCIALS
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

    source_system text default 'openregister',
    source_import_batch_id uuid references northdata_import_batches(id) on delete set null,
    source_raw_data jsonb,

    api_status text,
    notes text,

    enriched_at timestamptz default now(),
    updated_at timestamptz default now(),

    unique(openregister_company_id)
);

alter table company_financials
add column if not exists source_system text default 'openregister',
add column if not exists source_import_batch_id uuid references northdata_import_batches(id) on delete set null,
add column if not exists source_raw_data jsonb;

create index if not exists company_financials_company_register_id_idx
on company_financials(company_register_id);

create index if not exists company_financials_openregister_company_id_idx
on company_financials(openregister_company_id);


-- ============================================================
-- 5. SHAREHOLDERS / OWNERS
-- ============================================================

create table if not exists shareholders (
    id uuid primary key default gen_random_uuid(),

    company_register_id text not null references companies(register_id) on delete cascade,
    openregister_company_id text not null references companies(openregister_company_id) on delete cascade,
    company_name text,

    owner_key text not null,
    owner_id text,
    owner_type text,
    relation_type text,

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
-- 6. COMPANY UBOS
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
-- 7. COMPANY MODELS
-- Claude business model summaries.
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
-- 8. FIT SCORES
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
-- 9. PROCESSING LOGS
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
-- 10. UPDATED_AT TRIGGER
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
-- 11. MASTER OVERVIEW VIEW
-- Client-facing overview.
--
-- Important:
-- - No LEI in overview.
-- - No recommended_action in overview.
-- - No static main_owner/main_ubo fields here.
--   The Google Apps Script cockpit/dropdowns handle dynamic owner/UBO selection.
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

    c.company_data_source,
    c.financial_data_source,
    c.source,
    c.northdata_match_status,
    c.northdata_match_confidence,
    c.northdata_match_method,

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

    cf.report_count,
    cf.latest_report_start_date,
    cf.latest_report_end_date,
    cf.source_system as financials_table_source,

    cm.business_segment as claude_business_segment,
    cm.summary as claude_detailed_business_segment,

    fs.fit_score,
    fs.fit_label,
    fs.fit_comment,
    fs.succession_signal,
    fs.financial_signal,
    fs.shareholder_signal,
    fs.risk_flags,

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
-- 12. RLS LOCKDOWN
-- Service role key can still access these from Streamlit backend.
-- ============================================================

alter table if exists openregister_search_runs enable row level security;
alter table if exists northdata_import_batches enable row level security;
alter table if exists northdata_import_rows enable row level security;
alter table if exists companies enable row level security;
alter table if exists company_financials enable row level security;
alter table if exists shareholders enable row level security;
alter table if exists company_ubos enable row level security;
alter table if exists company_models enable row level security;
alter table if exists company_fit_scores enable row level security;
alter table if exists processing_logs enable row level security;

revoke all on table openregister_search_runs from anon, authenticated;
revoke all on table northdata_import_batches from anon, authenticated;
revoke all on table northdata_import_rows from anon, authenticated;
revoke all on table companies from anon, authenticated;
revoke all on table company_financials from anon, authenticated;
revoke all on table shareholders from anon, authenticated;
revoke all on table company_ubos from anon, authenticated;
revoke all on table company_models from anon, authenticated;
revoke all on table company_fit_scores from anon, authenticated;
revoke all on table processing_logs from anon, authenticated;
revoke all on table master_overview from anon, authenticated;
