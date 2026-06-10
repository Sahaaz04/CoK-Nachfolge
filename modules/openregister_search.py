from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import openregister
from supabase import Client

from modules.openregister_client import get_openregister_client
from modules.utils import euro_to_cents, number_to_api_string, safe_to_dict, split_csv


LEGAL_FORM_OPTIONS: dict[str, str] = {
    "GmbH": "gmbh",
    "UG": "ug",
    "gGmbH": "ggmbh",
    "GmbH & Co. KG / KG": "kg",
    "OHG": "ohg",
    "e.K.": "ek",
    "GbR": "gbr",
    "AG": "ag",
    "SE": "se",
    "KGaA": "kgaa",
    "eG": "eg",
    "e.V.": "ev",
    "EWIV": "ewiv",
    "Foreign": "foreign",
    "LLP": "llp",
    "Municipal": "municipal",
    "Unknown": "unknown",
}

DEFAULT_LEGAL_FORM_LABELS = ["GmbH", "UG", "gGmbH", "GmbH & Co. KG / KG", "OHG", "e.K.", "GbR"]

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


@dataclass
class SearchResultSummary:
    search_run_id: str | None
    returned_companies: int
    saved_companies: int
    skipped_existing_companies: int
    companies: list[dict[str, Any]]
    errors: list[str]


def _bool_to_api(value: bool | None) -> str | None:
    if value is None:
        return None
    return "true" if value else "false"


def _add_range_filter(filters: list[dict[str, Any]], field: str, min_value: Any = None, max_value: Any = None, *, money: bool = False) -> None:
    min_api = euro_to_cents(min_value) if money else number_to_api_string(min_value)
    max_api = euro_to_cents(max_value) if money else number_to_api_string(max_value)

    payload: dict[str, Any] = {"field": field}
    if min_api is not None:
        payload["min"] = min_api
    if max_api is not None:
        payload["max"] = max_api

    if "min" in payload or "max" in payload:
        filters.append(payload)


def build_company_search_filters(
    *,
    active_only: bool = True,
    legal_form_values: list[str] | None = None,
    industry_codes: list[str] | None = None,
    purpose_keywords: list[str] | None = None,
    has_lei: bool | None = None,
    financial_ranges: dict[str, tuple[float | int | None, float | int | None]] | None = None,
    ownership_ranges: dict[str, tuple[float | int | None, float | int | None]] | None = None,
    ownership_booleans: dict[str, bool | None] | None = None,
) -> list[dict[str, Any]]:
    """Build OpenRegister advanced-search filters.

    OpenRegister expects values/min/max as strings. Monetary fields are cents,
    while the UI uses euros.
    """
    filters: list[dict[str, Any]] = []

    if active_only:
        filters.append({"field": "active", "value": "true"})

    if legal_form_values:
        filters.append({"field": "legal_form", "values": legal_form_values})

    if industry_codes:
        filters.append({"field": "industry_codes", "values": industry_codes})

    if purpose_keywords:
        filters.append({"field": "purpose", "keywords": purpose_keywords})

    if has_lei is not None:
        filters.append({"field": "has_lei", "value": _bool_to_api(has_lei)})

    for field, (min_value, max_value) in (financial_ranges or {}).items():
        _add_range_filter(filters, field, min_value, max_value, money=field in MONEY_FIELDS)

    for field, (min_value, max_value) in (ownership_ranges or {}).items():
        _add_range_filter(filters, field, min_value, max_value, money=False)

    for field, value in (ownership_booleans or {}).items():
        if value is not None:
            filters.append({"field": field, "value": _bool_to_api(value)})

    return filters


def create_search_run(
    supabase: Client,
    *,
    search_name: str,
    filters: list[dict[str, Any]],
    query: dict[str, Any] | None,
    pagination: dict[str, Any],
    requested_max_companies: int,
) -> str | None:
    payload = {
        "search_name": search_name or None,
        "filters_json": filters,
        "query_json": query,
        "pagination_json": pagination,
        "requested_max_companies": requested_max_companies,
        "api_status": "running",
    }
    response = supabase.table("openregister_search_runs").insert(payload).execute()
    rows = response.data or []
    return rows[0].get("id") if rows else None


def update_search_run(
    supabase: Client,
    search_run_id: str | None,
    **fields: Any,
) -> None:
    if not search_run_id:
        return
    supabase.table("openregister_search_runs").update(fields).eq("id", search_run_id).execute()


def _company_payload_from_search_item(item: Any, search_run_id: str | None) -> dict[str, Any]:
    raw = safe_to_dict(item)
    company_id = raw.get("company_id")
    return {
        "openregister_company_id": company_id,
        "register_id": company_id,
        "name": raw.get("name"),
        "legal_form": raw.get("legal_form"),
        "active": raw.get("active"),
        "country": raw.get("country"),
        "register_number": raw.get("register_number"),
        "register_court": raw.get("register_court"),
        "register_type": raw.get("register_type"),
        "source": "openregister_search",
        "last_search_run_id": search_run_id,
        "raw_search_result": raw,
    }


def save_search_companies_to_supabase(
    supabase: Client,
    companies: list[Any],
    search_run_id: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    payloads = []
    for item in companies:
        payload = _company_payload_from_search_item(item, search_run_id)
        if payload.get("openregister_company_id"):
            payloads.append(payload)

    if not payloads:
        return 0, []

    response = (
        supabase.table("companies")
        .upsert(payloads, on_conflict="openregister_company_id")
        .execute()
    )
    return len(response.data or []), response.data or []


def log_processing_event(
    supabase: Client,
    *,
    search_run_id: str | None = None,
    module: str,
    endpoint: str | None = None,
    status: str,
    message: str | None = None,
    error_message: str | None = None,
    raw_data: dict[str, Any] | None = None,
) -> None:
    supabase.table("processing_logs").insert(
        {
            "search_run_id": search_run_id,
            "module": module,
            "endpoint": endpoint,
            "status": status,
            "message": message,
            "error_message": error_message,
            "raw_data": raw_data,
        }
    ).execute()


def run_company_search_and_save(
    *,
    supabase: Client,
    api_key_override: str | None,
    search_name: str,
    filters: list[dict[str, Any]],
    query_text: str | None = None,
    max_companies: int = 100,
    per_page: int = 100,
) -> SearchResultSummary:
    """Run OpenRegister search with pagination and upsert results into Supabase."""
    client = get_openregister_client(api_key_override=api_key_override)

    max_companies = max(1, int(max_companies))
    per_page = max(1, min(int(per_page), 100))

    query = {"value": query_text.strip()} if query_text and query_text.strip() else None
    first_pagination = {"page": 1, "per_page": per_page}

    search_run_id = create_search_run(
        supabase,
        search_name=search_name,
        filters=filters,
        query=query,
        pagination=first_pagination,
        requested_max_companies=max_companies,
    )

    all_items: list[Any] = []
    errors: list[str] = []

    try:
        page = 1
        while len(all_items) < max_companies:
            response = client.search.find_companies_v1(
                filters=filters,
                query=query,
                pagination={"page": page, "per_page": per_page},
            )
            data = safe_to_dict(response)
            items = getattr(response, "results", None) or data.get("results") or []
            all_items.extend(items)

            pagination = data.get("pagination") or {}
            total_pages = pagination.get("total_pages")
            if not items:
                break
            if total_pages is not None and page >= int(total_pages):
                break
            if len(all_items) >= max_companies:
                break
            page += 1

        all_items = all_items[:max_companies]
        saved_count, saved_rows = save_search_companies_to_supabase(supabase, all_items, search_run_id)

        update_search_run(
            supabase,
            search_run_id,
            returned_companies=len(all_items),
            saved_companies=saved_count,
            skipped_existing_companies=max(0, len(all_items) - saved_count),
            api_status="success",
        )
        log_processing_event(
            supabase,
            search_run_id=search_run_id,
            module="openregister_search",
            endpoint="search.find_companies_v1",
            status="success",
            message=f"Returned {len(all_items)} companies and saved/upserted {saved_count} rows.",
            raw_data={"filters": filters, "query": query},
        )
        return SearchResultSummary(search_run_id, len(all_items), saved_count, max(0, len(all_items) - saved_count), saved_rows, errors)

    except openregister.APIError as exc:
        status_code = getattr(exc, "status_code", None)
        message = f"OpenRegister API error: {exc}"
        if status_code:
            message = f"OpenRegister API error {status_code}: {exc}"
        errors.append(message)
        update_search_run(supabase, search_run_id, api_status="error", error_message=message)
        log_processing_event(
            supabase,
            search_run_id=search_run_id,
            module="openregister_search",
            endpoint="search.find_companies_v1",
            status="error",
            error_message=message,
            raw_data={"filters": filters, "query": query},
        )
        return SearchResultSummary(search_run_id, 0, 0, 0, [], errors)
    except Exception as exc:
        message = f"Unexpected search error: {exc}"
        errors.append(message)
        update_search_run(supabase, search_run_id, api_status="error", error_message=message)
        log_processing_event(
            supabase,
            search_run_id=search_run_id,
            module="openregister_search",
            endpoint="search.find_companies_v1",
            status="error",
            error_message=message,
            raw_data={"filters": filters, "query": query},
        )
        return SearchResultSummary(search_run_id, 0, 0, 0, [], errors)


def parse_codes_from_text(text: str | None) -> list[str]:
    return split_csv(text)


def parse_keywords_from_text(text: str | None) -> list[str]:
    return split_csv(text)
