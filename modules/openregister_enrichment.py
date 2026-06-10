from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from modules.openregister_client import get_openregister_client
from modules.utils import calculate_age, cents_to_eur, model_to_dict, owner_key, safe_get, ubo_key


def log_event(supabase, **payload: Any) -> None:
    try:
        supabase.table("processing_logs").insert(payload).execute()
    except Exception:
        pass


def fetch_companies_for_enrichment(supabase, *, limit: int = 50) -> list[dict[str, Any]]:
    res = (
        supabase.table("companies")
        .select("openregister_company_id,register_id,name,company_info_enriched_at,financials_enriched_at,ownership_enriched_at,ubos_enriched_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return getattr(res, "data", None) or []


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
        "industry_codes": raw.get("industry_codes"),
        "financials_date": indicator.get("date"),
        "revenue_eur": cents_to_eur(indicator.get("revenue")),
        "employees": indicator.get("employees"),
        "balance_sheet_total_eur": cents_to_eur(indicator.get("balance_sheet_total")),
        "net_income_eur": cents_to_eur(indicator.get("net_income")),
        "equity_eur": cents_to_eur(indicator.get("equity")),
        "cash_eur": cents_to_eur(indicator.get("cash")),
        "liabilities_eur": cents_to_eur(indicator.get("liabilities")),
        "real_estate_eur": cents_to_eur(indicator.get("real_estate")),
        "capital_amount_eur": capital.get("amount"),
        "raw_company_details": raw,
        "company_info_enriched_at": datetime.now(timezone.utc).isoformat(),
    }


def enrich_company_info(client, supabase, company: dict[str, Any], *, update_existing: bool) -> dict[str, Any]:
    company_id = company["openregister_company_id"]
    if company.get("company_info_enriched_at") and not update_existing:
        return {"status": "skipped", "endpoint": "company_info"}
    raw = model_to_dict(client.company.get_details_v1(company_id, realtime=False))
    payload = normalize_company_details(raw)
    # postgrest does not accept now() as special string in payload. Use RPC default by omitting not possible, so set via database server timestamp not here.
    payload.pop("company_info_enriched_at", None)
    payload["company_info_enriched_at"] = datetime.now(timezone.utc).isoformat()
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
    }
    supabase.table("company_financials").upsert(payload, on_conflict="openregister_company_id").execute()
    supabase.table("companies").update({"financials_enriched_at": datetime.now(timezone.utc).isoformat()}).eq("openregister_company_id", company_id).execute()
    return {"status": "success", "endpoint": "financials"}


def normalize_owner_row(company: dict[str, Any], owner: dict[str, Any], sources: list[dict[str, Any]], best_available: bool, index: int) -> dict[str, Any]:
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
        "relation_start_date": owner.get("start"),
        "best_available": best_available,
        "sources_json": sources,
        "api_status": "success",
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
        "has_majority_owner": largest is not None and largest >= 50,
        "largest_owner_percentage": largest,
    }


def enrich_ownership(client, supabase, company: dict[str, Any], *, update_existing: bool, best_available: bool = False) -> dict[str, Any]:
    company_id = company["openregister_company_id"]
    if company.get("ownership_enriched_at") and not update_existing:
        return {"status": "skipped", "endpoint": "ownership"}
    raw = model_to_dict(client.company.get_owners_v1(company_id, realtime=False, best_available=best_available))
    owners = raw.get("owners") or []
    sources = raw.get("sources") or []
    rows = [normalize_owner_row(company, owner, sources, raw.get("best_available"), i) for i, owner in enumerate(owners)]
    if update_existing:
        supabase.table("shareholders").delete().eq("openregister_company_id", company_id).execute()
    if rows:
        supabase.table("shareholders").upsert(rows, on_conflict="openregister_company_id,owner_key").execute()
    supabase.table("companies").update({**_owner_summary(rows), "ownership_enriched_at": datetime.now(timezone.utc).isoformat()}).eq("openregister_company_id", company_id).execute()
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
    supabase.table("companies").update({"ubos_enriched_at": datetime.now(timezone.utc).isoformat()}).eq("openregister_company_id", company_id).execute()
    return {"status": "success", "endpoint": "ubos", "ubos": len(rows)}


def run_enrichment(
    *,
    api_key: str,
    supabase,
    limit: int,
    update_existing: bool,
    fetch_company_info: bool,
    fetch_financials: bool,
    fetch_ownership: bool,
    fetch_ubos: bool,
    best_available_owners: bool = False,
) -> dict[str, Any]:
    client = get_openregister_client(api_key)
    companies = fetch_companies_for_enrichment(supabase, limit=limit)
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
                    outcome = fn(client, supabase, company, update_existing=update_existing, best_available=best_available_owners)
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
                results.append({"company": company_name, "company_id": company_id, "endpoint": endpoint, "status": "error", "error": str(exc)})
    return {"companies_seen": len(companies), "results": results}
