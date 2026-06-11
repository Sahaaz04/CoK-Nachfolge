-- Run this in Supabase SQL editor if your tables already exist.
-- It refreshes only the master_overview view; it does not alter stored table data.

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
