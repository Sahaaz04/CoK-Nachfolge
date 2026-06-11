from __future__ import annotations

import json
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


def _drop_sheet_excluded_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append({k: v for k, v in row.items() if k not in EXCLUDED_SHEET_COLUMNS})
    return cleaned


def _safe_sheet_cell(value: Any, *, column_name: str | None = None) -> Any:
    """Convert values to Sheets-safe cells and protect against 50k char limit."""
    if column_name == "industry_codes":
        value = format_industry_codes(value)
    else:
        value = flatten_for_sheet(value)
    if isinstance(value, str) and len(value) > MAX_SHEET_CELL_CHARS:
        return value[:MAX_SHEET_CELL_CHARS] + "… [truncated for Google Sheets cell limit; full value stays in Supabase]"
    return value



def _style_header_row(worksheet, column_count: int) -> None:
    """Make Sheet headers readable: frozen, bold, and theme-colored."""
    try:
        worksheet.freeze(rows=1)
    except Exception:
        pass
    try:
        worksheet.format(
            "1:1",
            {
                "backgroundColor": {"red": 0.1098, "green": 0.3608, "blue": 0.3608},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "horizontalAlignment": "CENTER",
            },
        )
    except Exception:
        pass
    try:
        worksheet.set_basic_filter()
    except Exception:
        pass


def _write_rows(worksheet, rows: list[dict[str, Any]]) -> int:
    worksheet.clear()
    rows = _drop_sheet_excluded_columns(rows)

    if not rows:
        worksheet.update([["No rows"]], value_input_option="USER_ENTERED")
        return 0

    df = pd.DataFrame(rows)
    for col in df.columns:
        df[col] = df[col].map(lambda value, col=col: _safe_sheet_cell(value, column_name=col))

    values = [df.columns.tolist()] + df.astype(object).where(pd.notnull(df), "").values.tolist()
    worksheet.update(values, value_input_option="USER_ENTERED")
    _style_header_row(worksheet, len(df.columns))
    return len(rows)


def sync_supabase_to_google_sheets(supabase) -> dict[str, int]:
    credentials = _get_credentials()
    gc = gspread.authorize(credentials)
    spreadsheet = gc.open_by_key(_get_sheet_id())

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
