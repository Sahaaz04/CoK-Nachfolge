from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from modules.openregister_client import get_openregister_client
from modules.utils import calculate_age, cents_to_eur, model_to_dict, owner_key, ubo_key


def log_event(supabase, **payload: Any) -> None:
    try:
        supabase.table("processing_logs").insert(payload).execute()
    except Exception:
        pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_year(value: Any) -> int | None:
    if value is None:
        return None

    text = str(value)
    match = re.search(r"([12][0-9]{3})", text)
    if not match:
        return None

    try:
        return int(match.group(1))
    except Exception:
        return None


def fetch_companies_for_enrichment(supabase, *, page_size: int = 1000, hard_cap: int = 50000) -> list[dict[str, Any]]:
    """Fetch saved companies for enrichment without exposing a UI limit.

    Supabase/PostgREST paginates responses, so read in chunks. hard_cap is only
    a safety valve to avoid accidental infinite/huge jobs if something goes wrong.
    """
    rows: list[dict[str, Any]] = []
    start = 0

    while len(rows) < hard_cap:
        end = min(start + page_size - 1, hard_cap - 1)
        res = (
            supabase.table("companies")
            .select(
                "openregister_company_id,register_id,name,"
                "company_info_enriched_at,financials_enriched_at,"
                "ownership_enriched_at,ubos_enriched_at"
            )
            .order("created_at", desc=True)
            .range(start, end)
            .execute()
        )

        batch = getattr(res, "data", None) or []
        if not batch:
            break

        rows.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return rows


def _latest_indicator(indicators: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not indicators:
        return None

    return sorted(indicators, key=lambda x: x.get("date") or "", reverse=True)[0]


def normalize_company_details(raw: dict[str, Any]) -> dict[str, Any]:
    address = raw.get("address") or {}
    contact = raw.get("contact") or {}
    name_obj = raw.get("name") or {}
    purpose = raw.get("purpose") or {}
    capital = raw.get("capital") or {}
    indicator = _latest_indicator(raw.get("indicators") or []) or {}

    return {
        "name": name_obj.get("name") or raw.get("name") or raw.get("id"),
        "legal_form": raw.get("legal_form") or name_obj.get("legal_form"),

        # Company founding/incorporation year.
        # This is the company's own founding year, not shareholder start year.
        "founding_year": extract_year(raw.get("incorporated_at")),

        "status": raw.get("status"),
        "active": True if raw.get("status") == "active" else False if raw.get("status") else None,
        "city": address.get("city"),
        "postal_code": address.get("postal_code"),
        "street": address.get("street"),
        "formatted_address": address.get("formatted_value"),
        "website": contact.get("website_url"),
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "vat_id": contact.get("vat_id"),
        "lei": raw.get("lei"),
        "purpose": purpose.get("purpose") if isinstance(purpose, dict) else None,

        # Source-specific OpenRegister fields.
        # These should not be mixed with NorthData fields.
        "openregister_wz_codes": raw.get("industry_codes"),
        "openregister_revenue_eur": cents_to_eur(indicator.get("revenue")),
        "openregister_financials_date": indicator.get("date"),

        # OpenRegister-only comparison fields from latest raw_company_details.indicators row.
        "openregister_employees": indicator.get("employees"),
        "openregister_balance_sheet_total_eur": cents_to_eur(indicator.get("balance_sheet_total")),
        "openregister_net_income_eur": cents_to_eur(indicator.get("net_income")),
        "openregister_cash_eur": cents_to_eur(indicator.get("cash")),
        "openregister_liabilities_eur": cents_to_eur(indicator.get("liabilities")),

        # Shared financial/company fields.
        # These are protected from overwrite for NorthData-imported rows below.
        "financials_date": indicator.get("date"),
        "employees": indicator.get("employees"),
        "balance_sheet_total_eur": cents_to_eur(indicator.get("balance_sheet_total")),
        "net_income_eur": cents_to_eur(indicator.get("net_income")),
        "equity_eur": cents_to_eur(indicator.get("equity")),
        "cash_eur": cents_to_eur(indicator.get("cash")),
        "liabilities_eur": cents_to_eur(indicator.get("liabilities")),
        "real_estate_eur": cents_to_eur(indicator.get("real_estate")),
        "capital_amount_eur": capital.get("amount"),

        "raw_company_details": raw,
        "company_info_enriched_at": now_iso(),
    }


def enrich_company_info(client, supabase, company: dict[str, Any], *, update_existing: bool) -> dict[str, Any]:
    company_id = company["openregister_company_id"]

    if company.get("company_info_enriched_at") and not update_existing:
        return {"status": "skipped", "endpoint": "company_info"}

    raw = model_to_dict(client.company.get_details_v1(company_id, realtime=False))
    payload = normalize_company_details(raw)

    payload.pop("company_info_enriched_at", None)
    payload["company_info_enriched_at"] = now_iso()

    existing_res = (
        supabase.table("companies")
        .select(
            "source,"
            "name,legal_form,country,register_number,register_court,register_type,"
            "status,active,city,postal_code,street,website,email,phone,vat_id,purpose,"
            "financials_date,openregister_financials_date,capital_amount_eur,balance_sheet_total_eur,net_income_eur,"
            "northdata_revenue_eur,openregister_revenue_eur,"
            "openregister_employees,openregister_balance_sheet_total_eur,"
            "openregister_net_income_eur,openregister_cash_eur,openregister_liabilities_eur,"
            "equity_eur,employees,cash_eur,liabilities_eur,real_estate_eur,"
            "northdata_wz_code,openregister_wz_codes"
        )
        .eq("openregister_company_id", company_id)
        .limit(1)
        .execute()
    )

    existing_rows = getattr(existing_res, "data", None) or []
    existing = existing_rows[0] if existing_rows else {}

    # These are fields NorthData can provide.
    # If the company came from NorthData and the field already has a value,
    # OpenRegister company_info must not overwrite it.
    #
    # Do NOT include OpenRegister-specific fields here:
    # - openregister_revenue_eur
    # - openregister_wz_codes
    # - openregister_employees
    # - openregister_balance_sheet_total_eur
    # - openregister_net_income_eur
    # - openregister_cash_eur
    # - openregister_liabilities_eur
    #
    # Those are source-specific OpenRegister fields and should be filled.
    northdata_protected_fields = [
        "name",
        "legal_form",
        "country",
        "register_number",
        "register_court",
        "register_type",
        "status",
        "active",
        "city",
        "postal_code",
        "street",
        "website",
        "email",
        "phone",
        "vat_id",
        "purpose",
        "financials_date",
        "capital_amount_eur",
        "balance_sheet_total_eur",
        "net_income_eur",
        "equity_eur",
        "employees",
        "cash_eur",
        "liabilities_eur",
        "real_estate_eur",
    ]

    if existing.get("source") == "northdata_import":
        for field in northdata_protected_fields:
            if existing.get(field) is not None:
                payload.pop(field, None)

    # Never overwrite any existing value with NULL/blank from OpenRegister.
    for field, value in list(payload.items()):
        if field in {"raw_company_details", "company_info_enriched_at"}:
            continue

        if value is None:
            payload.pop(field, None)
            continue

        if isinstance(value, str) and not value.strip():
            payload.pop(field, None)
            continue

    supabase.table("companies").update(payload).eq("openregister_company_id", company_id).execute()

    return {"status": "success", "endpoint": "company_info"}


def enrich_financials(client, supabase, company: dict[str, Any], *, update_existing: bool) -> dict[str, Any]:
    company_id = company["openregister_company_id"]
    register_id = company.get("register_id") or company_id

    if company.get("financials_enriched_at") and not update_existing:
        return {"status": "skipped", "endpoint": "financials"}

    raw = model_to_dict(client.company.get_financials_v1(company_id))
    reports = raw.get("reports") or []
    latest = sorted(reports, key=lambda r: r.get("report_end_date") or "", reverse=True)[0] if reports else {}

    payload = {
        "company_register_id": register_id,
        "openregister_company_id": company_id,
        "company_name": company.get("name"),
        "report_count": len(reports),
        "latest_report_start_date": latest.get("report_start_date"),
        "latest_report_end_date": latest.get("report_end_date"),
        "raw_financials": raw,
        "api_status": "success",
        "enriched_at": now_iso(),
    }

    supabase.table("company_financials").upsert(payload, on_conflict="openregister_company_id").execute()
    supabase.table("companies").update({"financials_enriched_at": now_iso()}).eq("openregister_company_id", company_id).execute()

    return {"status": "success", "endpoint": "financials"}


def normalize_owner_row(
    company: dict[str, Any],
    owner: dict[str, Any],
    sources: list[dict[str, Any]],
    best_available: bool,
    index: int,
) -> dict[str, Any]:
    company_id = company["openregister_company_id"]
    natural = owner.get("natural_person") or {}
    legal = owner.get("legal_person") or {}
    dob = natural.get("date_of_birth")

    row = {
        "company_register_id": company.get("register_id") or company_id,
        "openregister_company_id": company_id,
        "company_name": company.get("name"),
        "owner_key": owner_key(company_id, owner, index),
        "owner_id": owner.get("id"),
        "owner_type": owner.get("type"),
        "relation_type": owner.get("relation_type"),
        "shareholder_name": owner.get("name"),
        "natural_person_full_name": natural.get("full_name"),
        "natural_person_first_name": natural.get("first_name"),
        "natural_person_last_name": natural.get("last_name"),
        "date_of_birth": dob,
        "age": calculate_age(dob),
        "legal_person_name": legal.get("name"),
        "owner_city": natural.get("city") or legal.get("city"),
        "owner_country": natural.get("country") or legal.get("country"),
        "nominal_share_eur": owner.get("nominal_share"),
        "percentage_share": owner.get("percentage_share"),
        "best_available": best_available,
        "sources_json": sources,
        "api_status": "success",
        "retrieved_at": now_iso(),
        "raw_data": owner,
    }

    return row


def _owner_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    natural_count = sum(1 for r in rows if r.get("owner_type") == "natural_person")
    legal_count = sum(1 for r in rows if r.get("owner_type") == "legal_person")
    ages = [r.get("age") for r in rows if r.get("age") is not None]
    percentages = [r.get("percentage_share") for r in rows if r.get("percentage_share") is not None]
    largest = max(percentages) if percentages else None

    return {
        "number_of_owners": len(rows),
        "natural_person_owner_count": natural_count,
        "legal_person_owner_count": legal_count,
        "youngest_owner_age": min(ages) if ages else None,
        "oldest_owner_age": max(ages) if ages else None,
        "has_sole_owner": len(rows) == 1 if rows else None,
        "has_majority_owner": largest is not None and largest > 50,
        "largest_owner_percentage": largest,
    }


def enrich_ownership(
    client,
    supabase,
    company: dict[str, Any],
    *,
    update_existing: bool,
    best_available: bool = False,
) -> dict[str, Any]:
    company_id = company["openregister_company_id"]

    if company.get("ownership_enriched_at") and not update_existing:
        return {"status": "skipped", "endpoint": "ownership"}

    raw = model_to_dict(client.company.get_owners_v1(company_id, realtime=False, best_available=best_available))
    owners = raw.get("owners") or []
    sources = raw.get("sources") or []

    rows = [
        normalize_owner_row(company, owner, sources, raw.get("best_available"), i)
        for i, owner in enumerate(owners)
    ]

    if update_existing:
        supabase.table("shareholders").delete().eq("openregister_company_id", company_id).execute()

    if rows:
        supabase.table("shareholders").upsert(rows, on_conflict="openregister_company_id,owner_key").execute()

    supabase.table("companies").update({
        **_owner_summary(rows),
        "ownership_enriched_at": now_iso(),
    }).eq("openregister_company_id", company_id).execute()

    return {"status": "success", "endpoint": "ownership", "owners": len(rows)}


def normalize_ubo_row(company: dict[str, Any], ubo: dict[str, Any], index: int) -> dict[str, Any]:
    company_id = company["openregister_company_id"]
    natural = ubo.get("natural_person") or {}
    legal = ubo.get("legal_person") or {}
    dob = natural.get("date_of_birth")

    return {
        "company_register_id": company.get("register_id") or company_id,
        "openregister_company_id": company_id,
        "company_name": company.get("name"),
        "ubo_key": ubo_key(company_id, ubo, index),
        "ubo_id": ubo.get("id"),
        "ubo_name": ubo.get("name"),
        "ubo_type": "natural_person" if natural else "legal_person" if legal else None,
        "percentage_share": ubo.get("percentage_share"),
        "max_percentage_share": ubo.get("max_percentage_share"),
        "natural_person_full_name": natural.get("full_name"),
        "natural_person_first_name": natural.get("first_name"),
        "natural_person_last_name": natural.get("last_name"),
        "date_of_birth": dob,
        "age": calculate_age(dob),
        "legal_person_name": legal.get("name"),
        "ubo_city": natural.get("city") or legal.get("city"),
        "ubo_country": natural.get("country") or legal.get("country"),
        "api_status": "success",
        "enriched_at": now_iso(),
        "raw_data": ubo,
    }


def enrich_ubos(client, supabase, company: dict[str, Any], *, update_existing: bool) -> dict[str, Any]:
    company_id = company["openregister_company_id"]

    if company.get("ubos_enriched_at") and not update_existing:
        return {"status": "skipped", "endpoint": "ubos"}

    raw = model_to_dict(client.company.get_ubos_v1(company_id))
    ubos = raw.get("ubos") or []

    rows = [normalize_ubo_row(company, ubo, i) for i, ubo in enumerate(ubos)]

    if update_existing:
        supabase.table("company_ubos").delete().eq("openregister_company_id", company_id).execute()

    if rows:
        supabase.table("company_ubos").upsert(rows, on_conflict="openregister_company_id,ubo_key").execute()

    supabase.table("companies").update({"ubos_enriched_at": now_iso()}).eq("openregister_company_id", company_id).execute()

    return {"status": "success", "endpoint": "ubos", "ubos": len(rows)}


def run_enrichment(
    *,
    api_key: str,
    supabase,
    update_existing: bool,
    fetch_company_info: bool,
    fetch_financials: bool,
    fetch_ownership: bool,
    fetch_ubos: bool,
) -> dict[str, Any]:
    client = get_openregister_client(api_key)
    companies = fetch_companies_for_enrichment(supabase)
    results = []

    for company in companies:
        company_id = company.get("openregister_company_id")
        company_name = company.get("name")

        for endpoint, enabled, fn in [
            ("company_info", fetch_company_info, enrich_company_info),
            ("financials", fetch_financials, enrich_financials),
            ("ownership", fetch_ownership, enrich_ownership),
            ("ubos", fetch_ubos, enrich_ubos),
        ]:
            if not enabled:
                continue

            try:
                if endpoint == "ownership":
                    outcome = fn(client, supabase, company, update_existing=update_existing, best_available=False)
                else:
                    outcome = fn(client, supabase, company, update_existing=update_existing)

                log_event(
                    supabase,
                    company_register_id=company.get("register_id"),
                    openregister_company_id=company_id,
                    company_name=company_name,
                    module="openregister_enrichment",
                    endpoint=endpoint,
                    status=outcome.get("status"),
                    message=str(outcome),
                )

                results.append({"company": company_name, "company_id": company_id, **outcome})

            except Exception as exc:
                log_event(
                    supabase,
                    company_register_id=company.get("register_id"),
                    openregister_company_id=company_id,
                    company_name=company_name,
                    module="openregister_enrichment",
                    endpoint=endpoint,
                    status="error",
                    error_message=str(exc),
                )

                results.append({
                    "company": company_name,
                    "company_id": company_id,
                    "endpoint": endpoint,
                    "status": "error",
                    "error": str(exc),
                })

    return {"companies_seen": len(companies), "results": results}
