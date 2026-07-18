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

        # OpenRegister-only financial/company-size fields from the latest
        # raw_company_details.indicators row. Each has a dedicated column -
        # never write these into the shared/NorthData columns.
        "openregister_employees": indicator.get("employees"),
        "openregister_balance_sheet_total_eur": cents_to_eur(indicator.get("balance_sheet_total")),
        "openregister_net_income_eur": cents_to_eur(indicator.get("net_income")),
        "openregister_equity_eur": cents_to_eur(indicator.get("equity")),
        "openregister_cash_eur": cents_to_eur(indicator.get("cash")),
        "openregister_liabilities_eur": cents_to_eur(indicator.get("liabilities")),
        "openregister_real_estate_eur": cents_to_eur(indicator.get("real_estate")),
        "openregister_capital_amount_eur": capital.get("amount"),

        "financials_date": indicator.get("date"),

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
            "financials_date,openregister_financials_date,"
            "northdata_capital_amount_eur,openregister_capital_amount_eur,"
            "northdata_balance_sheet_total_eur,openregister_balance_sheet_total_eur,"
            "northdata_net_income_eur,openregister_net_income_eur,"
            "northdata_revenue_eur,openregister_revenue_eur,"
            "northdata_equity_eur,openregister_equity_eur,"
            "northdata_employees,openregister_employees,"
            "northdata_cash_eur,openregister_cash_eur,"
            "northdata_liabilities_eur,openregister_liabilities_eur,"
            "northdata_real_estate_eur,openregister_real_estate_eur,"
            "northdata_wz_code,openregister_wz_codes"
        )
        .eq("openregister_company_id", company_id)
        .limit(1)
        .execute()
    )

    existing_rows = getattr(existing_res, "data", None) or []
    existing = existing_rows[0] if existing_rows else {}

    # These are fields NorthData can provide, and which still share a single
    # column between both sources (unlike the financial/employee fields
    # above, which each now have a dedicated northdata_/openregister_
    # column and never need this protection).
    # If the company came from NorthData and the field already has a value,
    # OpenRegister company_info must not overwrite it.
    #
    # Do NOT include OpenRegister-specific fields here:
    # - openregister_revenue_eur
    # - openregister_wz_codes
    # - openregister_employees
    # - openregister_balance_sheet_total_eur
    # - openregister_net_income_eur
    # - openregister_equity_eur
    # - openregister_cash_eur
    # - openregister_liabilities_eur
    # - openregister_real_estate_eur
    # - openregister_capital_amount_eur
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


# --- Helpers for extracting summary indicators from raw_financials ---

def _first_number(*values: Any) -> float | None:
    """Return the first non-null numeric value from candidates."""
    for v in values:
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _find_row_by_names(rows: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any] | None:
    """
    Find a top-level row in an aktiva/passiva/guv list whose formatted_name or name
    matches any of the given German labels (case-insensitive, prefix-tolerant).
    Falls back to searching one level of children if not found at the top.
    """
    def matches(row: dict[str, Any]) -> bool:
        label = (row.get("formatted_name") or row.get("name") or "").strip().lower()
        # Strip a leading roman-numeral or letter prefix ("A. ", "II. ", ...)
        label = re.sub(r"^[a-z]+\.\s*", "", label)
        return any(label.startswith(n.lower()) for n in names)

    for row in rows or []:
        if matches(row):
            return row

    for row in rows or []:
        for child in row.get("children", []) or []:
            if matches(child):
                return child

    return None


def _row_latest_value(row: dict[str, Any] | None) -> float | None:
    """Get the latest available value from a merged-rows entry (values dict keyed by date)."""
    if not row:
        return None
    values = row.get("values") or {}
    if not values:
        return None
    latest_key = max(values.keys())
    return _first_number(values.get(latest_key))


def _extract_openregister_indicators(raw: dict[str, Any]) -> dict[str, float | None]:
    """
    Pull summary indicators (balance sheet total, equity, cash, liabilities, net income)
    out of the raw get_financials_v1 response, converting cents to EUR.

    Prefers the latest single report (has clean current_value fields).
    Falls back to raw.merged (multi-year values dict) if the latest report is missing rows.
    """
    reports = raw.get("reports") or []
    latest_report = None
    if reports:
        latest_report = sorted(reports, key=lambda r: r.get("report_end_date") or "", reverse=True)[0]

    aktiva_rows = (latest_report or {}).get("aktiva", {}).get("rows", []) if latest_report else []
    passiva_rows = (latest_report or {}).get("passiva", {}).get("rows", []) if latest_report else []
    guv_rows = (latest_report or {}).get("guv", {}).get("rows", []) if latest_report else []

    merged = raw.get("merged") or {}
    merged_aktiva_rows = (merged.get("aktiva") or {}).get("rows", [])
    merged_passiva_rows = (merged.get("passiva") or {}).get("rows", [])
    merged_guv_rows = (merged.get("guv") or {}).get("rows", [])

    def latest_report_value(row: dict[str, Any] | None) -> float | None:
        if not row:
            return None
        return _first_number(row.get("current_value"))

    def pick(rows: list[dict[str, Any]], merged_rows: list[dict[str, Any]], names: tuple[str, ...]) -> float | None:
        return _first_number(
            latest_report_value(_find_row_by_names(rows, names)),
            _row_latest_value(_find_row_by_names(merged_rows, names)),
        )

    balance_sheet_total = pick(aktiva_rows, merged_aktiva_rows, ("bilanzsumme",))
    equity = pick(passiva_rows, merged_passiva_rows, ("eigenkapital",))
    liabilities = pick(passiva_rows, merged_passiva_rows, ("verbindlichkeiten",))
    cash = pick(
        aktiva_rows, merged_aktiva_rows,
        ("kassenbestand", "kasse", "liquide mittel"),
    )
    net_income = pick(
        guv_rows, merged_guv_rows,
        ("jahresüberschuss", "jahresfehlbetrag", "jahresergebnis", "ergebnis nach steuern"),
    )

    return {
        "openregister_balance_sheet_total_eur": cents_to_eur(balance_sheet_total),
        "openregister_equity_eur": cents_to_eur(equity),
        "openregister_liabilities_eur": cents_to_eur(liabilities),
        "openregister_cash_eur": cents_to_eur(cash),
        "openregister_net_income_eur": cents_to_eur(net_income),
    }


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

    # Extract summary indicators from the raw response and update the companies table.
    # This is what makes the "Openregister X €" columns on the Overview sheet actually populate.
    indicators = _extract_openregister_indicators(raw)

    # Do not overwrite existing values with None on skip/no-data.
    company_update: dict[str, Any] = {
        k: v for k, v in indicators.items() if v is not None
    }
    company_update["financials_enriched_at"] = now_iso()

    if latest.get("report_end_date"):
        # Set openregister_financials_date only if we don't already have one (leave OpenRegister Import's value alone).
        if not company.get("openregister_financials_date"):
            company_update["openregister_financials_date"] = latest.get("report_end_date")

    supabase.table("companies").update(company_update).eq("openregister_company_id", company_id).execute()

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


def _ubo_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    natural_count = sum(1 for r in rows if r.get("ubo_type") == "natural_person")
    legal_count = sum(1 for r in rows if r.get("ubo_type") == "legal_person")
    ages = [r.get("age") for r in rows if r.get("age") is not None]

    return {
        "number_of_ubos": len(rows),
        "natural_person_ubo_count": natural_count,
        "legal_person_ubo_count": legal_count,
        "youngest_ubo_age": min(ages) if ages else None,
        "oldest_ubo_age": max(ages) if ages else None,
    }


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

    supabase.table("companies").update({
        **_ubo_summary(rows),
        "ubos_enriched_at": now_iso(),
    }).eq("openregister_company_id", company_id).execute()

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
