from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from modules.openregister_client import get_openregister_client
from modules.utils import model_to_dict


# NorthData columns we actually use because they match our existing schema.
# Extra NorthData-only columns are ignored.
COLUMN_ALIASES = {
    "name": ["Name"],
    "legal_form": ["Legal form", "Legal Form"],
    "country": ["Country"],
    "postal_code": ["Postal code", "Postal Code", "Postcode", "Zip", "ZIP"],
    "city": ["City"],
    "street": ["Street"],
    "register_court": ["Register court", "Register Court"],
    "northdata_register_id": ["Register ID", "Register Id", "Register id"],
    "status": ["Status"],
    "phone": ["Phone"],
    "email": ["Email"],
    "website": ["Website"],
    "vat_id": ["VAT Id", "VAT ID", "Vat Id", "VAT"],
    "purpose": ["Subject", "Purpose"],

    # NorthData industry/WZ source column.
    # This is kept separate from OpenRegister industry_codes/openregister_wz_codes.
    "northdata_wz_code": [
        "Industry segment (UKSIC)",
        "Industry Segment (UKSIC)",
        "Industry segment",
        "Industry Segment",
        "WZ code",
        "WZ Code",
        "WZ Codes",
        "NorthData WZ Code",
    ],

    "financials_date": ["Financials date", "Financials Date", "Financial Date"],

    "capital_amount_eur": [
        "Base/share capital EUR",
        "Base/share capital €",
        "Share capital EUR",
        "Share capital €",
        "Capital amount EUR",
        "Capital amount €",
    ],

    "balance_sheet_total_eur": [
        "Total assets EUR",
        "Total assets €",
        "Balance sheet total EUR",
        "Balance sheet total €",
        "Balance Sheet Total EUR",
        "Balance Sheet Total €",
        "Balance Sheet Tot",
    ],

    "net_income_eur": [
        "Earnings EUR",
        "Earnings €",
        "Net income EUR",
        "Net income €",
        "Net Income EUR",
        "Net Income €",
    ],

    # NorthData revenue is written only to northdata_revenue_eur.
    # We do not write revenue_eur here anymore.
    "northdata_revenue_eur": [
        "Revenue EUR",
        "Revenue €",
        "Revenue",
    ],

    "equity_eur": [
        "Equity EUR",
        "Equity €",
    ],

    "employees": [
        "Employee number",
        "Employee Number",
        "Employees",
    ],

    "cash_eur": [
        "Cash on hand EUR",
        "Cash on hand €",
        "Cash EUR",
        "Cash €",
    ],

    "liabilities_eur": [
        "Liabilities EUR",
        "Liabilities €",
    ],

    "real_estate_eur": [
        "Real estate EUR",
        "Real estate €",
        "Real Estate EUR",
        "Real Estate €",
    ],
}


LEGAL_FORM_MAP = {
    "gmbh": "gmbh",
    "gesellschaftmitbeschrankterhaftung": "gmbh",
    "gesellschaftmitbeschraenkterhaftung": "gmbh",

    "ug": "ug",
    "ughaftungsbeschrankt": "ug",
    "ughaftungsbeschraenkt": "ug",
    "unternehmergesellschaft": "ug",
    "unternehmergesellschaftmbh": "ug",

    "kg": "kg",
    "kommanditgesellschaft": "kg",
    "gmbhcokg": "kg",

    "ohg": "ohg",
    "offenehandelsgesellschaft": "ohg",

    "ek": "ek",
    "eingetragenerkaufmann": "ek",
    "eingetragenekauffrau": "ek",

    "ag": "ag",
    "aktiengesellschaft": "ag",

    "se": "se",
    "societaseuropaea": "se",
}


REGISTER_TYPE_MAP = {
    "HRB": "HRB",
    "HRA": "HRA",
    "PR": "PR",
    "GNR": "GnR",
    "VR": "VR",
}


NUMERIC_LOGICAL_FIELDS = [
    "capital_amount_eur",
    "balance_sheet_total_eur",
    "net_income_eur",
    "northdata_revenue_eur",
    "equity_eur",
    "employees",
    "cash_eur",
    "liabilities_eur",
    "real_estate_eur",
]


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def _normalize_for_compare(value: Any) -> str:
    """Normalize only for simple internal comparisons like legal-form mapping.

    This is not used to rewrite or guess register court names for OpenRegister.
    """
    text = _clean_text(value) or ""
    text = (
        text.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ß", "ss")
    )
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _find_col(row: dict[str, Any], logical_name: str) -> Any:
    aliases = COLUMN_ALIASES.get(logical_name, [])
    normalized_row = {_norm_key(k): v for k, v in row.items()}

    for alias in aliases:
        key = _norm_key(alias)
        if key in normalized_row:
            return normalized_row[key]

    return None


def _has_source_value(value: Any) -> bool:
    return _clean_text(value) is not None


def _parse_number(value: Any) -> float | None:
    """
    Handles both international and German number formats:
    35,307,989.85  -> 35307989.85
    35.307.989,85  -> 35307989.85
    55548.86       -> 55548.86
    55.548,86      -> 55548.86
    (1,234.56)     -> -1234.56
    """
    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None

    text = (
        text.replace("€", "")
        .replace("%", "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .strip()
    )

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = re.sub(r"[^0-9,.\-]", "", text)

    if not text:
        return None

    # If both comma and dot exist, the last separator is the decimal separator.
    if "," in text and "." in text:
        if text.rfind(".") > text.rfind(","):
            # 35,307,989.85
            text = text.replace(",", "")
        else:
            # 35.307.989,85
            text = text.replace(".", "").replace(",", ".")

    elif "," in text:
        parts = text.split(",")

        if len(parts) == 2 and len(parts[-1]) in {1, 2}:
            # 123,45
            text = text.replace(",", ".")
        else:
            # 1,234,567
            text = text.replace(",", "")

    elif "." in text:
        parts = text.split(".")

        if len(parts) > 2:
            # 1.234.567
            text = text.replace(".", "")

    try:
        number = float(text)
        return -number if negative else number
    except Exception:
        return None


def _parse_int(value: Any) -> int | None:
    number = _parse_number(value)
    if number is None:
        return None
    return int(round(number))


def _parse_date(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    # Excel serial date, e.g. 45657
    if isinstance(value, (int, float)):
        try:
            parsed = pd.to_datetime(value, unit="D", origin="1899-12-30", errors="coerce")
            if pd.notna(parsed):
                return parsed.date().isoformat()
        except Exception:
            pass

    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return _clean_text(value)
        return parsed.date().isoformat()
    except Exception:
        return _clean_text(value)


def _normalize_legal_form(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None

    key = _normalize_for_compare(text)
    return LEGAL_FORM_MAP.get(key)


def _parse_status(value: Any) -> tuple[str | None, bool | None]:
    status = _clean_text(value)
    if not status:
        return None, None

    key = status.lower()

    inactive_words = [
        "inactive",
        "deleted",
        "dissolved",
        "liquidated",
        "removed",
        "gelöscht",
        "geloescht",
        "aufgelöst",
        "aufgeloest",
        "erloschen",
        "liquidation",
    ]
    active_words = [
        "active",
        "currently registered",
        "registered",
        "aktiv",
        "eingetragen",
        "bestehend",
    ]

    if any(word in key for word in inactive_words):
        return status, False
    if any(word in key for word in active_words):
        return status, True

    return status, None


def parse_register_id(value: Any) -> tuple[str | None, str | None]:
    """
    NorthData example:
    "HRB 30469" -> ("HRB", "30469")
    "HRA 1234"  -> ("HRA", "1234")
    "GnR 12"    -> ("GnR", "12")
    """
    text = _clean_text(value)
    if not text:
        return None, None

    text = re.sub(r"\s+", " ", text.strip())
    match = re.match(r"^([A-Za-z]+)\s*([A-Za-z0-9./\- ]+)$", text)
    if not match:
        return None, text

    raw_type = match.group(1).strip()
    raw_number = match.group(2).strip()

    register_type = REGISTER_TYPE_MAP.get(raw_type.upper(), raw_type)
    register_number = re.sub(r"\s+", "", raw_number)

    return register_type, register_number


def _search_openregister_company(client, row_data: dict[str, Any]) -> dict[str, Any]:
    """Strict OpenRegister identity resolution.

    No fuzzy matching. No court-name normalization. No name fallback.
    NorthData rows are saved only when OpenRegister returns exactly one company
    for the exact register_type + register_number + register_court supplied.
    """
    register_type = row_data.get("register_type")
    register_number = row_data.get("register_number")
    register_court = row_data.get("register_court")

    if not register_type or not register_number or not register_court:
        return {
            "status": "missing_register_data",
            "company_id": None,
            "candidate": None,
            "message": "Missing register type, register number, or register court.",
        }

    try:
        response = client.search.find_companies_v1(
            filters=[
                {"field": "register_type", "value": register_type},
                {"field": "register_number", "value": register_number},
                {"field": "register_court", "value": register_court},
            ],
            pagination={"page": 1, "per_page": 10},
        )
        data = model_to_dict(response)
        results = data.get("results") or []

        if len(results) == 1:
            candidate = results[0]
            company_id = candidate.get("company_id")
            if not company_id:
                return {
                    "status": "missing_openregister_company_id",
                    "company_id": None,
                    "candidate": candidate,
                    "message": "OpenRegister returned one result, but it had no company_id.",
                }
            return {
                "status": "matched",
                "company_id": company_id,
                "candidate": candidate,
                "message": "Matched by exact register type, register number, and register court.",
            }

        if len(results) > 1:
            return {
                "status": "multiple_candidates",
                "company_id": None,
                "candidate": None,
                "message": "Multiple OpenRegister results found for exact register identity; skipped to avoid wrong match.",
                "candidates": results,
            }

        return {
            "status": "no_match",
            "company_id": None,
            "candidate": None,
            "message": "No OpenRegister match found for exact register identity.",
        }

    except Exception as exc:
        return {
            "status": "openregister_error",
            "company_id": None,
            "candidate": None,
            "message": str(exc),
        }


def _numeric_parse_warnings(row: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for logical_name in NUMERIC_LOGICAL_FIELDS:
        source_value = _find_col(row, logical_name)
        if not _has_source_value(source_value):
            continue

        parsed = _parse_int(source_value) if logical_name == "employees" else _parse_number(source_value)
        if parsed is None:
            warnings.append(f"{logical_name}: could not parse {source_value!r}")
    return warnings


def _northdata_row_to_company_payload(row: dict[str, Any], company_id: str, candidate: dict[str, Any] | None) -> dict[str, Any]:
    register_type, register_number = parse_register_id(_find_col(row, "northdata_register_id"))
    status, active = _parse_status(_find_col(row, "status"))

    payload: dict[str, Any] = {
        "openregister_company_id": company_id,
        "register_id": company_id,

        "name": _clean_text(_find_col(row, "name")) or (candidate or {}).get("name"),
        "legal_form": _normalize_legal_form(_find_col(row, "legal_form")) or (candidate or {}).get("legal_form"),
        "active": active if active is not None else (candidate or {}).get("active"),
        "country": _clean_text(_find_col(row, "country")) or (candidate or {}).get("country"),

        "register_number": register_number or (candidate or {}).get("register_number"),
        "register_court": _clean_text(_find_col(row, "register_court")) or (candidate or {}).get("register_court"),
        "register_type": register_type or (candidate or {}).get("register_type"),

        "status": status,
        "city": _clean_text(_find_col(row, "city")),
        "postal_code": _clean_text(_find_col(row, "postal_code")),
        "street": _clean_text(_find_col(row, "street")),
        "website": _clean_text(_find_col(row, "website")),
        "email": _clean_text(_find_col(row, "email")),
        "phone": _clean_text(_find_col(row, "phone")),
        "vat_id": _clean_text(_find_col(row, "vat_id")),
        "purpose": _clean_text(_find_col(row, "purpose")),

        # NorthData industry/WZ is kept separate from OpenRegister WZ.
        "northdata_wz_code": _clean_text(_find_col(row, "northdata_wz_code")),

        "financials_date": _parse_date(_find_col(row, "financials_date")),
        "capital_amount_eur": _parse_number(_find_col(row, "capital_amount_eur")),
        "balance_sheet_total_eur": _parse_number(_find_col(row, "balance_sheet_total_eur")),
        "net_income_eur": _parse_number(_find_col(row, "net_income_eur")),

        # NorthData revenue is source-specific now.
        "northdata_revenue_eur": _parse_number(_find_col(row, "northdata_revenue_eur")),

        "equity_eur": _parse_number(_find_col(row, "equity_eur")),
        "employees": _parse_int(_find_col(row, "employees")),
        "cash_eur": _parse_number(_find_col(row, "cash_eur")),
        "liabilities_eur": _parse_number(_find_col(row, "liabilities_eur")),
        "real_estate_eur": _parse_number(_find_col(row, "real_estate_eur")),

        "source": "northdata_import",
    }

    # Do not overwrite existing DB values with blanks.
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value

    return cleaned


def _read_excel(uploaded_file: Any) -> pd.DataFrame:
    """
    Reads NorthData .xlsx upload.
    Current requirements include openpyxl, so .xlsx is supported.
    """
    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    return pd.read_excel(uploaded_file, engine="openpyxl")


def _existing_company_by_openregister_id(supabase, company_id: str) -> dict[str, Any] | None:
    response = (
        supabase.table("companies")
        .select("id,openregister_company_id,register_id,name")
        .eq("openregister_company_id", company_id)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def run_northdata_import(
    *,
    uploaded_file: Any,
    openregister_api_key: str,
    supabase,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """
    Import NorthData Excel rows.

    Final rule:
    - No temp company ID.
    - No inserting unmatched companies.
    - No fuzzy/name fallback matching.
    - Every saved company must have real OpenRegister company_id.
    - If OpenRegister ID already exists, update existing row.
    - NorthData revenue/WZ write to northdata_* columns only.
    """
    if not openregister_api_key:
        raise ValueError("OpenRegister API key is required.")

    df = _read_excel(uploaded_file)
    df = df.where(pd.notnull(df), None)

    if max_rows is not None and max_rows > 0:
        df = df.head(max_rows)

    client = get_openregister_client(openregister_api_key)

    results: list[dict[str, Any]] = []
    imported = 0
    updated = 0
    skipped = 0
    errors = 0
    rows_with_parse_warnings = 0

    for index, row in df.iterrows():
        raw_row = row.to_dict()

        register_type, register_number = parse_register_id(_find_col(raw_row, "northdata_register_id"))
        row_data = {
            "row_number": int(index) + 2,
            "name": _clean_text(_find_col(raw_row, "name")),
            "legal_form": _normalize_legal_form(_find_col(raw_row, "legal_form")),
            "register_court": _clean_text(_find_col(raw_row, "register_court")),
            "register_type": register_type,
            "register_number": register_number,
        }

        try:
            parse_warnings = _numeric_parse_warnings(raw_row)
            if parse_warnings:
                rows_with_parse_warnings += 1

            match = _search_openregister_company(client, row_data)

            if match.get("status") != "matched" or not match.get("company_id"):
                skipped += 1
                results.append({
                    **row_data,
                    "status": match.get("status"),
                    "message": match.get("message"),
                    "parse_warnings": "; ".join(parse_warnings) if parse_warnings else None,
                })
                continue

            company_id = str(match["company_id"])
            candidate = match.get("candidate") or {}

            payload = _northdata_row_to_company_payload(raw_row, company_id, candidate)

            existing = _existing_company_by_openregister_id(supabase, company_id)

            supabase.table("companies").upsert(
                payload,
                on_conflict="openregister_company_id",
            ).execute()

            if existing:
                updated += 1
                action = "updated_existing"
            else:
                imported += 1
                action = "inserted_new"

            results.append({
                **row_data,
                "openregister_company_id": company_id,
                "status": action,
                "message": match.get("message"),
                "parse_warnings": "; ".join(parse_warnings) if parse_warnings else None,
            })

        except Exception as exc:
            errors += 1
            results.append({
                **row_data,
                "status": "error",
                "message": str(exc),
            })

    return {
        "total_rows": len(df),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "rows_with_parse_warnings": rows_with_parse_warnings,
        "results": results,
    }
