"""
OpenRegister advanced company search -> Supabase.

This module is Step 2 of the OpenRegister-first rebuild:
- Build OpenRegister filter payloads from simple app inputs
- Run paginated advanced company search
- Save every matched company into Supabase
- Guarantee one company row per OpenRegister company_id via upsert on openregister_company_id

No North Data dependency. No realtime calls. No enrichment calls here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import openregister
from openregister import Openregister


LogCallback = Optional[Callable[[str], None]]


# OpenRegister money filters expect cents. UI should ask for EUR.
MONEY_FILTER_FIELDS = {
    "revenue",
    "balance_sheet_total",
    "cash",
    "equity",
    "real_estate",
    "liabilities",
    "net_income",
    "capital_amount",
}


# These are the filter fields exposed in our MVP UI.
ALLOWED_FILTER_FIELDS = {
    "active",
    "legal_form",
    "industry_codes",
    "purpose",
    "has_lei",
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
    "has_sole_owner",
    "has_representative_owner",
    "is_family_owned",
    "youngest_owner_age",
}


LEGAL_FORM_LABELS = {
    "gmbh": "GmbH",
    "ug": "UG",
    "ggmbh": "gGmbH",
    "kg": "KG",
    "ohg": "OHG",
    "ek": "e.K.",
    "gbr": "GbR",
    "ag": "AG",
    "se": "SE",
    "kgaa": "KGaA",
    "eg": "eG",
    "ev": "e.V.",
    "ewiv": "EWIV",
    "foreign": "Foreign",
    "llp": "LLP",
    "municipal": "Municipal",
    "unknown": "Unknown",
}


DEFAULT_SUCCESSION_LEGAL_FORMS = ["gmbh", "ug", "ggmbh", "kg", "ohg", "ek", "gbr"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_openregister_client(api_key: Optional[str] = None) -> Openregister:
    """
    Create an OpenRegister SDK client.

    Priority:
    1. explicit api_key from Streamlit UI
    2. OPENREGISTER_API_KEY environment variable
    """
    resolved_key = (api_key or os.environ.get("OPENREGISTER_API_KEY") or "").strip()
    if not resolved_key:
        raise ValueError("Missing OpenRegister API key. Provide it in the app or OPENREGISTER_API_KEY.")

    return Openregister(
        api_key=resolved_key,
        max_retries=2,
        timeout=60.0,
    )


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_plain_dict(obj: Any) -> Dict[str, Any]:
    """
    Convert Stainless/Pydantic SDK models to plain JSON-ish dictionaries.
    """
    if obj is None:
        return {}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return obj
    return dict(obj)


def _to_cents_str(value_eur: Any) -> Optional[str]:
    """Convert a UI EUR value into OpenRegister cents string."""
    if value_eur in (None, ""):
        return None
    try:
        return str(int(round(float(value_eur) * 100)))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid EUR amount: {value_eur!r}")


def _to_number_str(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        # Keep integer-looking numbers clean; preserve decimals if needed.
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return str(number)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid numeric value: {value!r}")


def add_value_filter(filters: List[Dict[str, Any]], field: str, value: Any) -> None:
    if value in (None, ""):
        return
    if field not in ALLOWED_FILTER_FIELDS:
        raise ValueError(f"Unsupported OpenRegister filter field: {field}")

    if isinstance(value, bool):
        # SDK accepts SearchFilterBaseParam.value as string.
        value = "true" if value else "false"

    filters.append({"field": field, "value": str(value)})


def add_values_filter(filters: List[Dict[str, Any]], field: str, values: Iterable[Any]) -> None:
    clean_values = [str(v).strip() for v in values or [] if str(v).strip()]
    if not clean_values:
        return
    if field not in ALLOWED_FILTER_FIELDS:
        raise ValueError(f"Unsupported OpenRegister filter field: {field}")

    filters.append({"field": field, "values": clean_values})


def add_keywords_filter(filters: List[Dict[str, Any]], field: str, keywords: Iterable[Any]) -> None:
    clean_keywords = [str(v).strip() for v in keywords or [] if str(v).strip()]
    if not clean_keywords:
        return
    if field not in ALLOWED_FILTER_FIELDS:
        raise ValueError(f"Unsupported OpenRegister filter field: {field}")

    filters.append({"field": field, "keywords": clean_keywords})


def add_range_filter(
    filters: List[Dict[str, Any]],
    field: str,
    min_value: Any = None,
    max_value: Any = None,
    *,
    is_money_eur: bool = False,
) -> None:
    if min_value in (None, "") and max_value in (None, ""):
        return
    if field not in ALLOWED_FILTER_FIELDS:
        raise ValueError(f"Unsupported OpenRegister filter field: {field}")

    converter = _to_cents_str if is_money_eur else _to_number_str
    row: Dict[str, Any] = {"field": field}

    min_clean = converter(min_value)
    max_clean = converter(max_value)

    if min_clean is not None:
        row["min"] = min_clean
    if max_clean is not None:
        row["max"] = max_clean

    filters.append(row)


def build_company_search_filters(
    *,
    active_only: bool = True,
    legal_forms: Optional[List[str]] = None,
    industry_codes: Optional[List[str]] = None,
    purpose_keywords: Optional[List[str]] = None,
    has_lei: Optional[bool] = None,
    revenue_min_eur: Any = None,
    revenue_max_eur: Any = None,
    employees_min: Any = None,
    employees_max: Any = None,
    balance_sheet_total_min_eur: Any = None,
    balance_sheet_total_max_eur: Any = None,
    net_income_min_eur: Any = None,
    net_income_max_eur: Any = None,
    equity_min_eur: Any = None,
    equity_max_eur: Any = None,
    cash_min_eur: Any = None,
    cash_max_eur: Any = None,
    liabilities_min_eur: Any = None,
    liabilities_max_eur: Any = None,
    real_estate_min_eur: Any = None,
    real_estate_max_eur: Any = None,
    capital_amount_min_eur: Any = None,
    capital_amount_max_eur: Any = None,
    number_of_owners_min: Any = None,
    number_of_owners_max: Any = None,
    youngest_owner_age_min: Any = None,
    youngest_owner_age_max: Any = None,
    has_sole_owner: Optional[bool] = None,
    has_representative_owner: Optional[bool] = None,
    is_family_owned: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """
    Build the exact filter list accepted by client.search.find_companies_v1().

    Notes:
    - Money inputs are EUR in the UI; this function converts to cents.
    - Legal forms must use OpenRegister enum values such as 'gmbh', 'ug', 'kg'.
    - Ownership fields may require an OpenRegister Enterprise plan.
    """
    filters: List[Dict[str, Any]] = []

    if active_only:
        add_value_filter(filters, "active", True)

    add_values_filter(filters, "legal_form", legal_forms or [])
    add_values_filter(filters, "industry_codes", industry_codes or [])
    add_keywords_filter(filters, "purpose", purpose_keywords or [])

    if has_lei is not None:
        add_value_filter(filters, "has_lei", has_lei)

    add_range_filter(filters, "revenue", revenue_min_eur, revenue_max_eur, is_money_eur=True)
    add_range_filter(filters, "employees", employees_min, employees_max)
    add_range_filter(filters, "balance_sheet_total", balance_sheet_total_min_eur, balance_sheet_total_max_eur, is_money_eur=True)
    add_range_filter(filters, "net_income", net_income_min_eur, net_income_max_eur, is_money_eur=True)
    add_range_filter(filters, "equity", equity_min_eur, equity_max_eur, is_money_eur=True)
    add_range_filter(filters, "cash", cash_min_eur, cash_max_eur, is_money_eur=True)
    add_range_filter(filters, "liabilities", liabilities_min_eur, liabilities_max_eur, is_money_eur=True)
    add_range_filter(filters, "real_estate", real_estate_min_eur, real_estate_max_eur, is_money_eur=True)
    add_range_filter(filters, "capital_amount", capital_amount_min_eur, capital_amount_max_eur, is_money_eur=True)

    add_range_filter(filters, "number_of_owners", number_of_owners_min, number_of_owners_max)
    add_range_filter(filters, "youngest_owner_age", youngest_owner_age_min, youngest_owner_age_max)

    if has_sole_owner is not None:
        add_value_filter(filters, "has_sole_owner", has_sole_owner)
    if has_representative_owner is not None:
        add_value_filter(filters, "has_representative_owner", has_representative_owner)
    if is_family_owned is not None:
        add_value_filter(filters, "is_family_owned", is_family_owned)

    return filters


def create_search_run(
    supabase,
    *,
    search_name: str,
    filters: List[Dict[str, Any]],
    query: Optional[Dict[str, Any]],
    requested_max_companies: int,
) -> str:
    row = {
        "search_name": search_name,
        "filters_json": filters,
        "query_json": query or {},
        "pagination_json": {},
        "requested_max_companies": int(requested_max_companies),
        "api_status": "started",
    }
    response = supabase.table("openregister_search_runs").insert(row).execute()
    if not response.data:
        raise RuntimeError("Failed to create openregister_search_runs row.")
    return response.data[0]["id"]


def update_search_run(supabase, search_run_id: str, **fields: Any) -> None:
    supabase.table("openregister_search_runs").update(fields).eq("id", search_run_id).execute()


def log_processing(
    supabase,
    *,
    module: str,
    status: str,
    message: str = "",
    endpoint: str = "",
    error_message: str = "",
    openregister_company_id: str = "",
    company_register_id: str = "",
    company_name: str = "",
    search_run_id: Optional[str] = None,
    raw_data: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        supabase.table("processing_logs").insert({
            "module": module,
            "endpoint": endpoint,
            "status": status,
            "message": message,
            "error_message": error_message,
            "openregister_company_id": openregister_company_id or None,
            "company_register_id": company_register_id or None,
            "company_name": company_name or None,
            "search_run_id": search_run_id,
            "raw_data": raw_data or {},
        }).execute()
    except Exception:
        # Logs should never break the actual search flow.
        pass


def company_search_item_to_company_row(item: Any, *, search_run_id: Optional[str] = None) -> Dict[str, Any]:
    data = _to_plain_dict(item)
    company_id = _clean_str(data.get("company_id"))
    if not company_id:
        raise ValueError(f"Search result missing company_id: {data}")

    return {
        "openregister_company_id": company_id,
        # Keep compatibility with old app modules that use register_id as identity.
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
        "updated_at": now_iso(),
    }


def upsert_search_companies(
    supabase,
    company_rows: List[Dict[str, Any]],
    *,
    update_existing_companies: bool = True,
    log_callback: LogCallback = None,
) -> Dict[str, int]:
    """
    Save search results into companies table.

    If update_existing_companies=True:
        Upsert search fields into existing rows.
    If False:
        Existing companies are counted/skipped without overwriting their row.

    Dedupe guarantee comes from the DB unique constraint + on_conflict=openregister_company_id.
    """
    inserted = 0
    updated = 0
    skipped_existing = 0

    for row in company_rows:
        company_id = row["openregister_company_id"]
        existing = (
            supabase.table("companies")
            .select("id, openregister_company_id, name")
            .eq("openregister_company_id", company_id)
            .limit(1)
            .execute()
        ).data

        if existing and not update_existing_companies:
            skipped_existing += 1
            if log_callback:
                log_callback(f"Skipped existing company: {row.get('name')} | {company_id}")
            continue

        supabase.table("companies").upsert(
            row,
            on_conflict="openregister_company_id",
        ).execute()

        if existing:
            updated += 1
            action = "Updated"
        else:
            inserted += 1
            action = "Inserted"

        if log_callback:
            log_callback(f"{action}: {row.get('name')} | {company_id}")

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped_existing": skipped_existing,
    }


def run_openregister_company_search(
    supabase,
    *,
    api_key: str,
    search_name: str,
    filters: List[Dict[str, Any]],
    query_value: str = "",
    max_companies: int = 100,
    per_page: int = 100,
    update_existing_companies: bool = True,
    log_callback: LogCallback = None,
) -> Dict[str, Any]:
    """
    Run OpenRegister advanced company search and save results to Supabase.

    Returns a summary dict containing counts and companies_for_enrichment.
    """
    if max_companies <= 0:
        raise ValueError("max_companies must be greater than 0.")

    per_page = max(1, min(int(per_page), 100))
    max_companies = int(max_companies)

    query = {"value": query_value.strip()} if query_value and query_value.strip() else None
    client = get_openregister_client(api_key)

    search_run_id = create_search_run(
        supabase,
        search_name=search_name,
        filters=filters,
        query=query,
        requested_max_companies=max_companies,
    )

    all_rows: List[Dict[str, Any]] = []
    page = 1
    total_pages_seen: Optional[int] = None

    try:
        while len(all_rows) < max_companies:
            remaining = max_companies - len(all_rows)
            page_size = min(per_page, remaining)

            if log_callback:
                log_callback(f"OpenRegister search page {page} | page size {page_size}")

            response = client.search.find_companies_v1(
                filters=filters,
                query=query if query else openregister.omit,
                pagination={"page": page, "per_page": page_size},
            )

            response_dict = _to_plain_dict(response)
            results = response.results or []
            pagination_dict = response_dict.get("pagination", {}) or {}
            total_pages_seen = pagination_dict.get("total_pages") or total_pages_seen

            if not results:
                break

            for item in results:
                all_rows.append(company_search_item_to_company_row(item, search_run_id=search_run_id))
                if len(all_rows) >= max_companies:
                    break

            if total_pages_seen and page >= int(total_pages_seen):
                break
            if len(results) < page_size:
                break

            page += 1

        save_stats = upsert_search_companies(
            supabase,
            all_rows,
            update_existing_companies=update_existing_companies,
            log_callback=log_callback,
        )

        update_search_run(
            supabase,
            search_run_id,
            returned_companies=len(all_rows),
            saved_companies=save_stats["inserted"] + save_stats["updated"],
            skipped_existing_companies=save_stats["skipped_existing"],
            pagination_json={"last_page_read": page, "total_pages_seen": total_pages_seen},
            api_status="success",
        )

        log_processing(
            supabase,
            module="openregister_search",
            endpoint="/v1/search/company",
            status="success",
            message=f"Search completed. Returned {len(all_rows)} companies.",
            search_run_id=search_run_id,
            raw_data={"filters": filters, "query": query, "save_stats": save_stats},
        )

        return {
            "search_run_id": search_run_id,
            "returned_companies": len(all_rows),
            "inserted": save_stats["inserted"],
            "updated": save_stats["updated"],
            "skipped_existing": save_stats["skipped_existing"],
            "companies_for_enrichment": all_rows,
        }

    except openregister.APIStatusError as exc:
        error_message = f"OpenRegister API status error {exc.status_code}: {exc}"
        update_search_run(supabase, search_run_id, api_status="error", error_message=error_message)
        log_processing(
            supabase,
            module="openregister_search",
            endpoint="/v1/search/company",
            status="error",
            message="OpenRegister search failed.",
            error_message=error_message,
            search_run_id=search_run_id,
        )
        raise

    except Exception as exc:
        error_message = str(exc)
        update_search_run(supabase, search_run_id, api_status="error", error_message=error_message)
        log_processing(
            supabase,
            module="openregister_search",
            endpoint="/v1/search/company",
            status="error",
            message="OpenRegister search failed.",
            error_message=error_message,
            search_run_id=search_run_id,
        )
        raise
