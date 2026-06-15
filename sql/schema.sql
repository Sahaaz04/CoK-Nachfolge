create extension if not exists "pgcrypto";

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

create table if not exists company_models (
    id uuid primary key default gen_random_uuid(),
    company_register_id text not null references companies(register_id) on delete cascade,
    openregister_company_id text references companies(openregister_company_id) on delete cascade,
    company_name text,
    website text,
    model_provider text not null default 'claude',
    model_name text,
    business_segment text,
    summary text,
    api_status text,
    notes text,
    raw_data jsonb,
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    unique(company_register_id, model_provider)
);

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

create index if not exists companies_openregister_company_id_idx on companies(openregister_company_id);
create index if not exists companies_name_idx on companies(name);
create index if not exists companies_legal_form_idx on companies(legal_form);
create index if not exists companies_active_idx on companies(active);
create index if not exists company_financials_openregister_company_id_idx on company_financials(openregister_company_id);
create index if not exists shareholders_openregister_company_id_idx on shareholders(openregister_company_id);
create index if not exists shareholders_name_idx on shareholders(shareholder_name);
create index if not exists company_ubos_openregister_company_id_idx on company_ubos(openregister_company_id);
create index if not exists processing_logs_openregister_company_id_idx on processing_logs(openregister_company_id);
create index if not exists processing_logs_created_at_idx on processing_logs(created_at);

create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists set_companies_updated_at on companies;
create trigger set_companies_updated_at before update on companies for each row execute function set_updated_at();

drop trigger if exists set_company_financials_updated_at on company_financials;
create trigger set_company_financials_updated_at before update on company_financials for each row execute function set_updated_at();

drop trigger if exists set_shareholders_updated_at on shareholders;
create trigger set_shareholders_updated_at before update on shareholders for each row execute function set_updated_at();

drop trigger if exists set_company_ubos_updated_at on company_ubos;
create trigger set_company_ubos_updated_at before update on company_ubos for each row execute function set_updated_at();

drop trigger if exists set_company_models_updated_at on company_models;
create trigger set_company_models_updated_at before update on company_models for each row execute function set_updated_at();

drop trigger if exists set_company_fit_scores_updated_at on company_fit_scores;
create trigger set_company_fit_scores_updated_at before update on company_fit_scores for each row execute function set_updated_at();

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

alter table openregister_search_runs enable row level security;
alter table companies enable row level security;
alter table company_financials enable row level security;
alter table shareholders enable row level security;
alter table company_ubos enable row level security;
alter table company_models enable row level security;
alter table company_fit_scores enable row level security;
alter table processing_logs enable row level security;
