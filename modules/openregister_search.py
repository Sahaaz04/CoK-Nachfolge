from __future__ import annotations

from typing import Any

from modules.openregister_client import get_openregister_client
from modules.utils import model_to_dict, eur_to_cents

MONEY_FIELDS = {
    "revenue",
    "balance_sheet_total",
    "net_income",
    "equity",
    "cash",
    "liabilities",
    "real_estate",
    "capital_amount",
}
RANGE_FIELDS = [
    "revenue",
    "employees",
    "balance_sheet_total",
    "net_income",
    "equity",
    "cash",
    "liabilities",
    "real_estate",
    "capital_amount",
    "number_of_owners",
    "youngest_owner_age",
]


def validate_filter_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in RANGE_FIELDS:
        min_value = config.get(f"{field}_min")
        max_value = config.get(f"{field}_max")
        if min_value not in (None, "") and max_value not in (None, ""):
            try:
                if float(min_value) > float(max_value):
                    errors.append(f"{field}: minimum cannot be greater than maximum.")
            except Exception:
                errors.append(f"{field}: invalid range values.")

    if config.get("has_sole_owner") is True:
        min_owners = config.get("number_of_owners_min")
        max_owners = config.get("number_of_owners_max")
        if min_owners not in (None, "", 1) or max_owners not in (None, "", 1):
            errors.append("Sole owner = Yes means number_of_owners must be exactly 1.")

    if not config.get("legal_forms"):
        errors.append("Select at least one legal form.")

    return errors


def _add_range_filter(filters: list[dict[str, Any]], field: str, min_value: Any = None, max_value: Any = None, *, money: bool = False) -> None:
    item: dict[str, Any] = {"field": field}
    if min_value not in (None, ""):
        item["min"] = eur_to_cents(min_value) if money else str(min_value)
    if max_value not in (None, ""):
        item["max"] = eur_to_cents(max_value) if money else str(max_value)
    if "min" in item or "max" in item:
        filters.append(item)


def build_filters(config: dict[str, Any]) -> list[dict[str, Any]]:
    errors = validate_filter_config(config)
    if errors:
        raise ValueError("; ".join(errors))

    filters: list[dict[str, Any]] = []

    if config.get("active_only"):
        filters.append({"field": "active", "value": "true"})

    legal_forms = config.get("legal_forms") or []
    if legal_forms:
        filters.append({"field": "legal_form", "values": legal_forms})

    industry_codes = config.get("industry_codes") or []
    if industry_codes:
        filters.append({"field": "industry_codes", "values": industry_codes})

    purpose_keywords = config.get("purpose_keywords") or []
    if purpose_keywords:
        filters.append({"field": "purpose", "keywords": purpose_keywords})

    if config.get("has_lei") is not None:
        filters.append({"field": "has_lei", "value": "true" if config["has_lei"] else "false"})

    for field in RANGE_FIELDS:
        _add_range_filter(
            filters,
            field,
            config.get(f"{field}_min"),
            config.get(f"{field}_max"),
            money=field in MONEY_FIELDS,
        )

    for field in ["has_sole_owner", "has_representative_owner", "is_family_owned"]:
        value = config.get(field)
        if value is not None:
            filters.append({"field": field, "value": "true" if value else "false"})

    return filters


def create_search_run(supabase, *, search_name: str, filters: list[dict[str, Any]], max_companies: int) -> str | None:
    payload = {
        "search_name": search_name or "OpenRegister filter search",
        "filters_json": filters,
        "pagination_json": {"max_companies": max_companies},
        "requested_max_companies": max_companies,
        "api_status": "started",
    }
    res = supabase.table("openregister_search_runs").insert(payload).execute()
    rows = getattr(res, "data", None) or []
    return rows[0].get("id") if rows else None


def update_search_run(supabase, run_id: str | None, **fields: Any) -> None:
    if not run_id:
        return
    supabase.table("openregister_search_runs").update(fields).eq("id", run_id).execute()


def log_event(supabase, **payload: Any) -> None:
    try:
        supabase.table("processing_logs").insert(payload).execute()
    except Exception:
        pass


def normalize_search_item(item: Any, search_run_id: str | None) -> dict[str, Any]:
    data = model_to_dict(item)
    company_id = data.get("company_id")
    return {
        "openregister_company_id": company_id,
        "register_id": company_id,
        "name": data.get("name"),
        "legal_form": data.get("legal_form"),
        "active": data.get("active"),
        "country": data.get("country"),
        "register_number": data.get("register_number"),
        "register_court": data.get("register_court"),
        "register_type": data.get("register_type"),
        "source": "openregister_search",
        "last_search_run_id": search_run_id,
        "raw_search_result": data,
    }


def run_company_search(
    *,
    api_key: str,
    supabase,
    search_name: str,
    filter_config: dict[str, Any],
    max_companies: int,
    per_page: int = 100,
) -> dict[str, Any]:
    client = get_openregister_client(api_key)
    filters = build_filters(filter_config)
    run_id = create_search_run(supabase, search_name=search_name, filters=filters, max_companies=max_companies)

    all_items: list[Any] = []
    page = 1
    try:
        while len(all_items) < max_companies:
            current_per_page = min(per_page, max_companies - len(all_items))
            response = client.search.find_companies_v1(
                filters=filters,
                pagination={"page": page, "per_page": current_per_page},
            )
            data = model_to_dict(response)
            results = data.get("results") or []
            if not results:
                break
            all_items.extend(results)

            pagination = data.get("pagination") or {}
            total_pages = pagination.get("total_pages")
            if total_pages and page >= int(total_pages):
                break
            if len(results) < current_per_page:
                break
            page += 1

        rows = [normalize_search_item(item, run_id) for item in all_items if item.get("company_id")]
        saved = 0
        if rows:
            # One company only once. Supabase unique constraint + upsert do the dedupe.
            supabase.table("companies").upsert(rows, on_conflict="openregister_company_id").execute()
            saved = len(rows)

        update_search_run(
            supabase,
            run_id,
            api_status="success",
            returned_companies=len(all_items),
            saved_companies=saved,
        )
        log_event(
            supabase,
            search_run_id=run_id,
            module="openregister_search",
            endpoint="/v1/search/company",
            status="success",
            message=f"Saved {saved} companies from search.",
        )
        return {"ok": True, "run_id": run_id, "filters": filters, "returned": len(all_items), "saved": saved, "rows": rows}
    except Exception as exc:
        update_search_run(supabase, run_id, api_status="error", error_message=str(exc))
        log_event(
            supabase,
            search_run_id=run_id,
            module="openregister_search",
            endpoint="/v1/search/company",
            status="error",
            error_message=str(exc),
        )
        return {"ok": False, "run_id": run_id, "filters": filters, "error": str(exc), "returned": 0, "saved": 0, "rows": []}
