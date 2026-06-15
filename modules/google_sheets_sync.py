from __future__ import annotations

import json
import re
from typing import Any

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

from modules.utils import flatten_for_sheet, format_industry_codes

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Google Sheets has a 50,000-character limit per cell. Keep a small buffer.
MAX_SHEET_CELL_CHARS = 49_000

# Raw API payloads belong in Supabase, not Google Sheets. These are often huge
# JSON blobs and can easily break the Google Sheets per-cell limit.
EXCLUDED_SHEET_COLUMNS = {
    "raw_search_result",
    "raw_company_details",
    "raw_financials",
    "raw_data",
    "sources_json",  # source objects can also be large; keep them in Supabase
}

# Technical / duplicate columns hidden from Sheets and Excel exports. The app
# still keeps them in Supabase for joins and backend logic.
DISPLAY_EXCLUDED_COLUMNS = EXCLUDED_SHEET_COLUMNS | {
    "register_id",          # same as OpenRegister company id in this app
    "company_register_id",  # duplicate helper key for child tables
    "lei",                  # user requested hidden
    "recommended_action",   # stored in DB, hidden from client sheets/export
}

HEADER_LABELS = {
    "id": "Row ID",
    "openregister_company_id": "Company ID",
    "company_name": "Company Name",
    "name": "Company Name",
    "legal_form": "Legal Form",
    "active": "Active",
    "country": "Country",
    "register_number": "Register Number",
    "register_court": "Register Court",
    "register_type": "Register Type",
    "postal_code": "Postal Code",
    "formatted_address": "Formatted Address",
    "vat_id": "VAT ID",
    "purpose": "Purpose",
    "industry_codes": "Industry Codes",
    "financials_date": "Financials Date",
    "revenue_eur": "Revenue €",
    "employees": "Employees",
    "balance_sheet_total_eur": "Balance Sheet Total €",
    "net_income_eur": "Net Income €",
    "equity_eur": "Equity €",
    "cash_eur": "Cash €",
    "liabilities_eur": "Liabilities €",
    "real_estate_eur": "Real Estate €",
    "capital_amount_eur": "Capital Amount €",
    "number_of_owners": "Number of Owners",
    "natural_person_owner_count": "Natural Person Owner Count",
    "legal_person_owner_count": "Legal Person Owner Count",
    "youngest_owner_age": "Youngest Owner Age",
    "oldest_owner_age": "Oldest Owner Age",
    "has_sole_owner": "Has Sole Owner",
    "has_representative_owner": "Owner Managed",
    "is_family_owned": "Family Owned",
    "has_majority_owner": "Has Majority Owner",
    "largest_owner_percentage": "Largest Owner %",
    "main_owner_name": "Main Owner Name",
    "main_owner_type": "Main Owner Type",
    "main_owner_percentage_share": "Main Owner %",
    "main_ubo_name": "Main UBO Name",
    "main_ubo_age": "Main UBO Age",
    "main_ubo_percentage_share": "Main UBO %",
    "main_ubo_max_percentage_share": "Main UBO Max %",
    "claude_business_segment": "Claude Business Segment",
    "claude_detailed_business_segment": "Detailed Claude Business Segment",
    "fit_score": "Fit Score",
    "fit_label": "Fit Label",
    "fit_comment": "Fit Comment",
    "recommended_action": "Recommended Action",
    "report_count": "Report Count",
    "latest_report_start_date": "Latest Report Start Date",
    "latest_report_end_date": "Latest Report End Date",
    "financials_date": "Financials Date",
    "api_status": "API Status",
    "model_provider": "Model Provider",
    "model_name": "Model Name",
    "business_segment": "Business Segment",
    "summary": "Detailed Claude Business Segment",
    "risk_flags": "Risk Flags",
    "succession_signal": "Succession Signal",
    "financial_signal": "Financial Signal",
    "shareholder_signal": "Shareholder Signal",
    "created_at": "Created At",
    "updated_at": "Updated At",
    "enriched_at": "Enriched At",
    "retrieved_at": "Retrieved At",
    "ubo_name": "UBO Name",
    "ubo_type": "UBO Type",
    "ubo_city": "UBO City",
    "ubo_country": "UBO Country",
    "percentage_share": "Percentage Share",
    "max_percentage_share": "Max Percentage Share",
    "shareholder_name": "Shareholder Name",
    "owner_type": "Owner Type",
    "owner_city": "Owner City",
    "owner_country": "Owner Country",
    "nominal_share_eur": "Nominal Share €",
    "relation_type": "Relation Type",
    "date_of_birth": "Date of Birth",
    "website": "Website",
    "email": "Email",
    "phone": "Phone",
    "notes": "Notes",
}

SHEET_TABLES = [
    ("Overview", "master_overview"),
    ("Companies", "companies"),
    ("Owners", "shareholders"),
    ("UBO Control Chain", "company_ubos"),
    ("Company Models", "company_models"),
    ("Fit Scores", "company_fit_scores"),
    ("Search Runs", "openregister_search_runs"),
    ("Logs", "processing_logs"),
]


def nice_sheet_header(column_name: str) -> str:
    """Convert snake_case DB columns to client-friendly spreadsheet headers."""
    if column_name in HEADER_LABELS:
        return HEADER_LABELS[column_name]
    label = str(column_name or "").replace("_", " ").strip().title()
    fixes = {
        " Id": " ID",
        "Id ": "ID ",
        "Id": "ID",
        "Api": "API",
        "Url": "URL",
        "Ubo": "UBO",
        "Lei": "LEI",
        "Eur": "€",
        "Vat": "VAT",
        "Json": "JSON",
    }
    for src, dst in fixes.items():
        label = label.replace(src, dst)
    return re.sub(r"\s+", " ", label).strip()


def _round_if_needed(value: Any, column_name: str | None = None) -> Any:
    if column_name in {"main_ubo_max_percentage_share", "max_percentage_share"} and value not in (None, ""):
        try:
            return round(float(value), 2)
        except Exception:
            return value
    return value


def _get_credentials() -> Credentials:
    raw = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON in Streamlit secrets.")
    info = json.loads(raw) if isinstance(raw, str) else dict(raw)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _get_sheet_id() -> str:
    sheet_id = st.secrets.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID in Streamlit secrets.")
    return sheet_id


def _fetch_all(supabase, table_or_view: str, limit: int = 5000) -> list[dict[str, Any]]:
    res = supabase.table(table_or_view).select("*").limit(limit).execute()
    return getattr(res, "data", None) or []


def _fetch_financials_sheet_rows(supabase, limit: int = 5000) -> list[dict[str, Any]]:
    """Build a readable Financials sheet from companies + financial metadata.

    Full raw financial JSON stays in Supabase. The Sheet gets only summary
    financial columns plus report metadata.
    """
    companies = _fetch_all(supabase, "companies", limit=limit)
    financials = _fetch_all(supabase, "company_financials", limit=limit)
    fin_by_company = {row.get("openregister_company_id"): row for row in financials}

    rows: list[dict[str, Any]] = []
    for company in companies:
        company_id = company.get("openregister_company_id")
        fin = fin_by_company.get(company_id, {}) or {}
        rows.append({
            "company_register_id": company.get("register_id"),
            "openregister_company_id": company_id,
            "company_name": company.get("name"),
            "financials_date": company.get("financials_date"),
            "revenue_eur": company.get("revenue_eur"),
            "employees": company.get("employees"),
            "balance_sheet_total_eur": company.get("balance_sheet_total_eur"),
            "net_income_eur": company.get("net_income_eur"),
            "equity_eur": company.get("equity_eur"),
            "cash_eur": company.get("cash_eur"),
            "liabilities_eur": company.get("liabilities_eur"),
            "real_estate_eur": company.get("real_estate_eur"),
            "capital_amount_eur": company.get("capital_amount_eur"),
            "report_count": fin.get("report_count"),
            "latest_report_start_date": fin.get("latest_report_start_date"),
            "latest_report_end_date": fin.get("latest_report_end_date"),
            "api_status": fin.get("api_status"),
            "notes": fin.get("notes"),
            "enriched_at": fin.get("enriched_at"),
            "updated_at": fin.get("updated_at"),
        })
    return rows


def _get_or_create_worksheet(spreadsheet, title: str, rows: int = 1000, cols: int = 30):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def _sheet_id(worksheet) -> int | None:
    return getattr(worksheet, "id", None) or getattr(worksheet, "_properties", {}).get("sheetId")


def _clear_values(worksheet) -> None:
    """Clear old values. Formatting is reset later in one batched request."""
    worksheet.clear()


def _column_letter(index_1_based: int) -> str:
    letters = ""
    n = index_1_based
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _numeric_format_requests(sheet_id: int, columns: list[str]) -> list[dict[str, Any]]:
    integer_columns = {
        "employees",
        "number_of_owners",
        "natural_person_owner_count",
        "legal_person_owner_count",
        "youngest_owner_age",
        "oldest_owner_age",
        "main_ubo_age",
        "age",
        "report_count",
        "fit_score",
        "requested_max_companies",
        "returned_companies",
        "saved_companies",
        "skipped_existing_companies",
    }
    decimal_columns = {
        "revenue_eur",
        "balance_sheet_total_eur",
        "net_income_eur",
        "equity_eur",
        "cash_eur",
        "liabilities_eur",
        "real_estate_eur",
        "capital_amount_eur",
        "largest_owner_percentage",
        "main_owner_percentage_share",
        "main_ubo_percentage_share",
        "main_ubo_max_percentage_share",
        "nominal_share_eur",
        "percentage_share",
        "max_percentage_share",
    }
    requests: list[dict[str, Any]] = []
    for idx, col in enumerate(columns):
        if col not in integer_columns and col not in decimal_columns:
            continue
        pattern = "0" if col in integer_columns else "#,##0.##"
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": idx,
                    "endColumnIndex": idx + 1,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })
    return requests


def _apply_sheet_formatting(worksheet, columns: list[str], row_count: int) -> None:
    """Apply only requested formatting in one Sheets API write request.

    We clear stale formats and then apply header + numeric formats. We do not
    add Google Sheets filters or frozen rows by default.
    """
    sheet_id = _sheet_id(worksheet)
    if sheet_id is None or not columns:
        return

    column_count = len(columns)
    _ = row_count  # kept for call readability; no default filters are applied.
    requests: list[dict[str, Any]] = [
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id},
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 0}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {"clearBasicFilter": {"sheetId": sheet_id}},
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.1098, "green": 0.3608, "blue": 0.3608},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        },
    ]
    requests.extend(_numeric_format_requests(sheet_id, columns))
    try:
        worksheet.spreadsheet.batch_update({"requests": requests})
    except Exception:
        pass


def _delete_worksheet_if_exists(spreadsheet, title: str) -> None:
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return
    try:
        spreadsheet.del_worksheet(worksheet)
    except Exception:
        # Do not block sync if a legacy sheet cannot be deleted. The current
        # canonical sheet will still be written as UBO Control Chain.
        pass


def _drop_sheet_excluded_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append({k: v for k, v in row.items() if k not in DISPLAY_EXCLUDED_COLUMNS})
    return cleaned


def _safe_sheet_cell(value: Any, *, column_name: str | None = None) -> Any:
    """Convert values to Sheets-safe cells and protect against 50k char limit."""
    if column_name == "industry_codes":
        value = format_industry_codes(value)
    else:
        value = flatten_for_sheet(value)
    value = _round_if_needed(value, column_name)
    if isinstance(value, str) and len(value) > MAX_SHEET_CELL_CHARS:
        return value[:MAX_SHEET_CELL_CHARS] + "… [truncated for Google Sheets cell limit; full value stays in Supabase]"
    return value


def _write_rows(worksheet, rows: list[dict[str, Any]]) -> int:
    _clear_values(worksheet)
    rows = _drop_sheet_excluded_columns(rows)

    if not rows:
        worksheet.update([["No rows"]], value_input_option="USER_ENTERED")
        return 0

    df = pd.DataFrame(rows)
    for col in df.columns:
        df[col] = df[col].map(lambda value, col=col: _safe_sheet_cell(value, column_name=col))

    headers = [nice_sheet_header(col) for col in df.columns.tolist()]
    values = [headers] + df.astype(object).where(pd.notnull(df), "").values.tolist()
    worksheet.update(values, value_input_option="USER_ENTERED")
    _apply_sheet_formatting(worksheet, df.columns.tolist(), len(rows))
    return len(rows)


def sync_supabase_to_google_sheets(supabase) -> dict[str, int]:
    credentials = _get_credentials()
    gc = gspread.authorize(credentials)
    spreadsheet = gc.open_by_key(_get_sheet_id())

    # v0.6 briefly used/left a legacy UBOs tab in some user sheets. The
    # canonical sheet is now UBO Control Chain, so remove the duplicate.
    _delete_worksheet_if_exists(spreadsheet, "UBOs")

    counts: dict[str, int] = {}
    for sheet_name, table in SHEET_TABLES:
        rows = _fetch_all(supabase, table)
        ws = _get_or_create_worksheet(spreadsheet, sheet_name)
        counts[sheet_name] = _write_rows(ws, rows)

        if sheet_name == "Companies":
            # Put the readable financial sheet next to Companies.
            fin_ws = _get_or_create_worksheet(spreadsheet, "Financials")
            counts["Financials"] = _write_rows(fin_ws, _fetch_financials_sheet_rows(supabase))
    return counts
