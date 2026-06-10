from __future__ import annotations

import json
from typing import Any

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

from modules.utils import flatten_for_sheet

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_TABLES = [
    ("Overview", "master_overview"),
    ("Companies", "companies"),
    ("Financials", "company_financials"),
    ("Owners", "shareholders"),
    ("UBOs", "company_ubos"),
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


def _get_or_create_worksheet(spreadsheet, title: str, rows: int = 1000, cols: int = 30):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def _write_rows(worksheet, rows: list[dict[str, Any]]) -> int:
    worksheet.clear()
    if not rows:
        worksheet.update([['No rows']], value_input_option="USER_ENTERED")
        return 0
    df = pd.DataFrame(rows)
    for col in df.columns:
        df[col] = df[col].map(flatten_for_sheet)
    values = [df.columns.tolist()] + df.astype(object).where(pd.notnull(df), "").values.tolist()
    worksheet.update(values, value_input_option="USER_ENTERED")
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
    return counts
