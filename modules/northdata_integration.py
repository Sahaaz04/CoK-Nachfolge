from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import pandas as pd
import streamlit as st

from modules.openregister_client import get_openregister_client
from modules.openregister_enrichment import enrich_ownership, enrich_ubos
from modules.utils import model_to_dict


REGISTER_ID_PATTERN = re.compile(
    r"\b(HRB|HRA|VR|GNR|GnR|PR)\s*([A-Z0-9./-]+)\b",
    re.IGNORECASE,
)

NORTHDATA_TARGET_FIELDS: dict[str, dict[str, Any]] = {
    "company_name": {
        "label": "Company Name",
        "required": True,
        "aliases": ["company name", "name", "firma", "unternehmen", "company", "legal name"],
    },
    "register_court": {
        "label": "Register Court",
        "required": True,
        "aliases": ["register court", "registergericht", "amtsgericht", "court"],
    },
    "register_id": {
        "label": "Register ID",
        "required": True,
        "aliases": ["register id", "registerid", "register", "registernummer", "handelsregister", "hrb", "hra"],
    },
    "legal_form": {
        "label": "Legal Form",
        "required": False,
        "aliases": ["legal form", "rechtsform", "form"],
    },
    "city": {
        "label": "City",
        "required": False,
        "aliases": ["city", "stadt", "ort"],
    },
    "postal_code": {
        "label": "Postal Code",
        "required": False,
        "aliases": ["postal code", "postcode", "zip", "plz"],
    },
    "country_code": {
        "label": "Country Code",
        "required": False,
        "aliases": ["country code", "country", "land", "country_code"],
    },
    "website": {
        "label": "Website",
        "required": False,
        "aliases": ["website", "url", "domain", "homepage", "web"],
    },
    "financials_date": {
        "label": "Financials Date / Year",
        "required": False,
        "aliases": ["financials date", "financial year", "fiscal year", "year", "date", "abschlussdatum"],
    },
    "revenue_eur": {
        "label": "Revenue EUR",
        "required": False,
        "aliases": ["revenue", "revenue eur", "umsatz", "sales"],
    },
    "employees": {
        "label": "Employees",
        "required": False,
        "aliases": ["employees", "employee count", "mitarbeiter", "anzahl mitarbeiter"],
    },
    "balance_sheet_total_eur": {
        "label": "Balance Sheet Total EUR",
        "required": False,
        "aliases": ["balance sheet total", "bilanzsumme", "total assets", "assets"],
    },
    "net_income_eur": {
        "label": "Net Income EUR",
        "required": False,
        "aliases": ["net income", "profit", "earnings", "jahresüberschuss", "net profit"],
    },
    "equity_eur": {
        "label": "Equity EUR",
        "required": False,
        "aliases": ["equity", "eigenkapital"],
    },
    "cash_eur": {
        "label": "Cash EUR",
        "required": False,
        "aliases": ["cash", "cash eur", "kasse", "liquid funds"],
    },
    "liabilities_eur": {
        "label": "Liabilities EUR",
        "required": False,
        "aliases": ["liabilities", "verbindlichkeiten"],
    },
}

LEGAL_FORM_MAP = {
    "gmbh": "gmbh",
    "ug": "ug",
    "unternehmergesellschaft": "ug",
    "kg": "kg",
    "gmbh & co. kg": "kg",
    "gmbh co kg": "kg",
    "ohg": "ohg",
    "e.k.": "ek",
    "e.k": "ek",
    "ek": "ek",
    "ag": "ag",
    "se": "se",
    "kgaa": "kgaa",
    "eg": "eg",
    "e.g.": "eg",
    "ev": "ev",
    "e.v.": "ev",
    "gbr": "gbr",
    "ggmbh": "ggmbh",
    "ewiv": "ewiv",
    "llp": "llp",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()




def json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("\n", " ").strip()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value).lower()).strip()


def normalize_company_name(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"\b(gmbh|ug|haftungsbeschränkt|mbh|kg|ohg|ag|se|eg|e\.k\.|ek|e\.v\.|ev|gbr|ggmbh)\b", " ", text)
    text = re.sub(r"[^a-z0-9äöüß]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_register_id(value: Any) -> tuple[str | None, str | None]:
    text = clean_text(value)
    if not text:
        return None, None
    match = REGISTER_ID_PATTERN.search(text)
    if not match:
        return None, None
    register_type = match.group(1).upper()
    if register_type == "GNR":
        register_type = "GnR"
    register_number = match.group(2).strip()
    return register_type, register_number


def parse_number(value: Any) -> float | None:
    if value is None or value == "":
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)

    text = clean_text(value)

    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None

    text = (
        text.replace("€", "")
        .replace("EUR", "")
        .replace("eur", "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .strip()
    )

    # Keep only digits, decimal/thousand separators, and minus sign.
    text = re.sub(r"[^0-9,.\-]", "", text)

    if not text:
        return None

    # Case 1: both comma and dot exist.
    # German: 1.234.567,89  -> 1234567.89
    # English: 1,234,567.89 -> 1234567.89
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            # Last separator is comma => comma is decimal, dots are thousands.
            text = text.replace(".", "").replace(",", ".")
        else:
            # Last separator is dot => dot is decimal, commas are thousands.
            text = text.replace(",", "")

    # Case 2: only comma exists.
    elif "," in text:
        parts = text.split(",")

        # Multiple commas usually means thousands / Indian grouping:
        # 5,200,000 or 7,00,000 -> remove commas.
        if len(parts) > 2:
            text = "".join(parts)

        # Single comma:
        # 123,45 likely decimal comma
        # 123,456 likely thousands
        else:
            before, after = parts

            if len(after) == 3 and before.replace("-", "").isdigit():
                text = before + after
            else:
                text = before + "." + after

    # Case 3: only dot exists.
    elif "." in text:
        parts = text.split(".")

        # Multiple dots usually means German thousands:
        # 1.234.567 -> 1234567
        if len(parts) > 2 and all(len(part) == 3 for part in parts[1:]):
            text = "".join(parts)

    try:
        return float(text)
    except Exception:
        return None


def parse_int(value: Any) -> int | None:
    number = parse_number(value)
    if number is None:
        return None
    return int(round(number))


def normalize_legal_form(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    cleaned = text.replace("gesellschaft mit beschränkter haftung", "gmbh")
    for key, val in sorted(LEGAL_FORM_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if key in cleaned:
            return val
    return text


def guess_column(columns: list[str], aliases: list[str]) -> str | None:
    normalized_columns = {normalize_text(col).replace("_", " "): col for col in columns}
    for alias in aliases:
        alias_norm = normalize_text(alias).replace("_", " ")
        if alias_norm in normalized_columns:
            return normalized_columns[alias_norm]
    for alias in aliases:
        alias_norm = normalize_text(alias).replace("_", " ")
        for col_norm, original in normalized_columns.items():
            if alias_norm in col_norm or col_norm in alias_norm:
                return original
    return None


def normalize_column_key(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9äöüß]+",
        "",
        clean_text(value).lower(),
    )


def resolve_typed_column_name(typed_value: str | None, columns: list[str]) -> str | None:
    typed = clean_text(typed_value)

    if not typed:
        return None

    typed_key = normalize_column_key(typed)

    if not typed_key:
        return None

    # Exact normalized match.
    normalized_map = {
        normalize_column_key(col): col
        for col in columns
    }

    if typed_key in normalized_map:
        return normalized_map[typed_key]

    # Safe partial match only when exactly one column matches.
    possible_matches = [
        col
        for col in columns
        if typed_key in normalize_column_key(col)
        or normalize_column_key(col) in typed_key
    ]

    if len(possible_matches) == 1:
        return possible_matches[0]

    return None


def column_mapping_ui(df: pd.DataFrame) -> dict[str, str | None]:
    columns = list(df.columns)
    mapping: dict[str, str | None] = {}

    st.subheader("Column mapping")
    st.caption(
        "Type the uploaded NorthData column name for each internal field. "
        "Leave blank to ignore optional fields. The app pre-fills guesses, but you can edit them manually."
    )

    with st.expander("Uploaded file columns", expanded=False):
        st.write(columns)

    for target, config in NORTHDATA_TARGET_FIELDS.items():
        guessed = guess_column(columns, config["aliases"]) or ""

        typed_value = st.text_input(
            config["label"] + (" *" if config["required"] else ""),
            value=guessed,
            placeholder="Type exact uploaded column name",
            key=f"northdata_map_{target}",
        )

        resolved_column = resolve_typed_column_name(typed_value, columns)
        mapping[target] = resolved_column

        if clean_text(typed_value) and not resolved_column:
            st.warning(
                f"Could not find uploaded column '{typed_value}' for {config['label']}."
            )

        elif resolved_column and clean_text(typed_value) != resolved_column:
            st.caption(f"Using uploaded column: `{resolved_column}`")

    return mapping


def validate_mapping(mapping: dict[str, str | None]) -> list[str]:
    missing = []
    for target, config in NORTHDATA_TARGET_FIELDS.items():
        if config["required"] and not mapping.get(target):
            missing.append(config["label"])
    return missing


def normalize_northdata_dataframe(df: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame:
    normalized = pd.DataFrame()
    for target in NORTHDATA_TARGET_FIELDS:
        source_col = mapping.get(target)
        normalized[target] = df[source_col] if source_col in df.columns else None

    normalized["company_name"] = normalized["company_name"].map(clean_text)
    normalized["register_court"] = normalized["register_court"].map(clean_text)
    normalized["register_id"] = normalized["register_id"].map(clean_text)
    normalized["city"] = normalized["city"].map(clean_text)
    normalized["postal_code"] = normalized["postal_code"].map(clean_text)
    normalized["country_code"] = normalized["country_code"].map(clean_text)
    normalized["website"] = normalized["website"].map(clean_text)
    normalized["financials_date"] = normalized["financials_date"].map(clean_text)
    normalized["legal_form"] = normalized["legal_form"].map(normalize_legal_form)

    parsed = normalized["register_id"].map(parse_register_id)
    normalized["register_type"] = parsed.map(lambda x: x[0])
    normalized["register_number"] = parsed.map(lambda x: x[1])

    for money_col in ["revenue_eur", "balance_sheet_total_eur", "net_income_eur", "equity_eur", "cash_eur", "liabilities_eur"]:
        normalized[money_col] = normalized[money_col].map(parse_number)
    normalized["employees"] = normalized["employees"].map(parse_int)

    return normalized


def read_uploaded_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file)


def create_import_batch(supabase, *, file_name: str, row_count: int, mapping: dict[str, str | None]) -> str | None:
    payload = {
        "file_name": file_name,
        "row_count": row_count,
        "column_mapping_json": mapping,
        "status": "started",
    }
    res = supabase.table("northdata_import_batches").insert(payload).execute()
    rows = getattr(res, "data", None) or []
    return rows[0].get("id") if rows else None


def update_import_batch(supabase, batch_id: str | None, **fields: Any) -> None:
    if not batch_id:
        return
    supabase.table("northdata_import_batches").update(fields).eq("id", batch_id).execute()


def search_openregister_candidates(client, *, company_name: str, register_court: str | None, register_type: str | None, register_number: str | None) -> tuple[list[dict[str, Any]], str]:
    if register_type and register_number and register_court:
        response = client.search.find_companies_v1(
            filters=[
                {"field": "register_court", "value": register_court},
                {"field": "register_type", "value": register_type},
                {"field": "register_number", "value": register_number},
            ],
            pagination={"page": 1, "per_page": 10},
        )
        results = model_to_dict(response).get("results") or []
        if results:
            return results, "exact_register_court_type_number"

    if register_type and register_number:
        kwargs = {
            "filters": [
                {"field": "register_type", "value": register_type},
                {"field": "register_number", "value": register_number},
            ],
            "pagination": {"page": 1, "per_page": 10},
        }
        if company_name:
            kwargs["query"] = {"value": company_name}
        response = client.search.find_companies_v1(**kwargs)
        results = model_to_dict(response).get("results") or []
        if results:
            return results, "register_type_number_name_query"

    if company_name:
        response = client.search.find_companies_v1(
            query={"value": company_name},
            pagination={"page": 1, "per_page": 10},
        )
        results = model_to_dict(response).get("results") or []
        return results, "company_name_query"

    return [], "no_search_fields"


def score_candidate(candidate: dict[str, Any], row: dict[str, Any], method: str) -> float:
    score = 0.0

    if row.get("register_type") and candidate.get("register_type") == row.get("register_type"):
        score += 25
    if row.get("register_number") and clean_text(candidate.get("register_number")) == clean_text(row.get("register_number")):
        score += 25

    court_a = normalize_text(candidate.get("register_court"))
    court_b = normalize_text(row.get("register_court"))
    if court_a and court_b:
        if court_a == court_b:
            score += 25
        elif court_a in court_b or court_b in court_a:
            score += 18
        else:
            score += 10 * SequenceMatcher(None, court_a, court_b).ratio()

    name_a = normalize_company_name(candidate.get("name"))
    name_b = normalize_company_name(row.get("company_name"))
    if name_a and name_b:
        score += 15 * SequenceMatcher(None, name_a, name_b).ratio()

    if row.get("legal_form") and candidate.get("legal_form") == row.get("legal_form"):
        score += 5

    if method == "exact_register_court_type_number":
        score = max(score, 95)

    return round(min(score, 100), 2)


def choose_best_match(candidates: list[dict[str, Any]], row: dict[str, Any], method: str) -> tuple[dict[str, Any] | None, float, str]:
    if not candidates:
        return None, 0.0, "no_match"
    scored = [(candidate, score_candidate(candidate, row, method)) for candidate in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0
    if best_score >= 85 and best_score - second_score >= 5:
        return best, best_score, "matched"
    if best_score >= 95 and len(scored) == 1:
        return best, best_score, "matched"
    return best, best_score, "manual_review"


def build_company_payload(row: dict[str, Any], candidate: dict[str, Any], batch_id: str | None, row_id: str | None, confidence: float, match_method: str) -> dict[str, Any]:
    company_id = candidate["company_id"]
    return {
        "openregister_company_id": company_id,
        "register_id": company_id,
        "name": row.get("company_name") or candidate.get("name"),
        "legal_form": row.get("legal_form") or candidate.get("legal_form"),
        "active": candidate.get("active"),
        "country": row.get("country_code") or candidate.get("country"),
        "register_number": row.get("register_number") or candidate.get("register_number"),
        "register_court": row.get("register_court") or candidate.get("register_court"),
        "register_type": row.get("register_type") or candidate.get("register_type"),
        "city": row.get("city"),
        "postal_code": row.get("postal_code"),
        "website": row.get("website"),
        "financials_date": row.get("financials_date"),
        "revenue_eur": row.get("revenue_eur"),
        "employees": row.get("employees"),
        "balance_sheet_total_eur": row.get("balance_sheet_total_eur"),
        "net_income_eur": row.get("net_income_eur"),
        "equity_eur": row.get("equity_eur"),
        "cash_eur": row.get("cash_eur"),
        "liabilities_eur": row.get("liabilities_eur"),
        "source": "northdata_upload",
        "financials_enriched_at": now_iso() if any(row.get(c) is not None for c in ["revenue_eur", "employees", "balance_sheet_total_eur", "net_income_eur", "equity_eur"]) else None,
        "raw_search_result": candidate,
        "northdata_import_batch_id": batch_id,
        "northdata_import_row_id": row_id,
        "northdata_raw_data": row.get("raw_json"),
        "northdata_match_status": "matched",
        "northdata_match_confidence": confidence,
        "northdata_match_method": match_method,
        "company_data_source": "northdata",
        "financial_data_source": "northdata",
    }


def upsert_northdata_financials(supabase, *, row: dict[str, Any], company_payload: dict[str, Any], batch_id: str | None) -> None:
    company_id = company_payload["openregister_company_id"]
    payload = {
        "company_register_id": company_id,
        "openregister_company_id": company_id,
        "company_name": company_payload.get("name"),
        "report_count": 1,
        "latest_report_end_date": row.get("financials_date"),
        "raw_financials": {
            "source": "northdata_upload",
            "normalized": {k: v for k, v in row.items() if k != "raw_json"},
            "raw": row.get("raw_json"),
        },
        "api_status": "northdata_upload",
        "notes": "Imported from NorthData upload.",
        "source_system": "northdata",
        "source_import_batch_id": batch_id,
        "source_raw_data": row.get("raw_json"),
        "enriched_at": now_iso(),
    }
    supabase.table("company_financials").upsert(payload, on_conflict="openregister_company_id").execute()


def process_northdata_import(
    *,
    supabase,
    api_key: str,
    file_name: str,
    raw_df: pd.DataFrame,
    normalized_df: pd.DataFrame,
    mapping: dict[str, str | None],
    max_rows: int | None = None,
    enrich_shareholders: bool = True,
    enrich_ubos_flag: bool = False,
    update_existing_enrichment: bool = False,
) -> dict[str, Any]:
    client = get_openregister_client(api_key)
    total_rows = len(normalized_df) if max_rows is None else min(len(normalized_df), max_rows)
    batch_id = create_import_batch(supabase, file_name=file_name, row_count=total_rows, mapping=mapping)

    counters = {
        "processed": 0,
        "matched": 0,
        "manual_review": 0,
        "unmatched": 0,
        "companies_upserted": 0,
        "shareholders_enriched": 0,
        "ubos_enriched": 0,
        "errors": 0,
    }
    results: list[dict[str, Any]] = []

    for i in range(total_rows):
        raw_row = json_safe(raw_df.iloc[i].where(pd.notna(raw_df.iloc[i]), None).to_dict())
        row = json_safe(normalized_df.iloc[i].where(pd.notna(normalized_df.iloc[i]), None).to_dict())
        row["raw_json"] = raw_row
        counters["processed"] += 1
        try:
            candidates, method = search_openregister_candidates(
                client,
                company_name=row.get("company_name") or "",
                register_court=row.get("register_court"),
                register_type=row.get("register_type"),
                register_number=row.get("register_number"),
            )
            best, confidence, status = choose_best_match(candidates, row, method)
            row_payload = {
                "batch_id": batch_id,
                "row_number": i + 1,
                "company_name": row.get("company_name"),
                "register_court": row.get("register_court"),
                "register_id": row.get("register_id"),
                "register_type": row.get("register_type"),
                "register_number": row.get("register_number"),
                "legal_form": row.get("legal_form"),
                "city": row.get("city"),
                "postal_code": row.get("postal_code"),
                "country_code": row.get("country_code"),
                "website": row.get("website"),
                "financials_date": row.get("financials_date"),
                "revenue_eur": row.get("revenue_eur"),
                "employees": row.get("employees"),
                "balance_sheet_total_eur": row.get("balance_sheet_total_eur"),
                "net_income_eur": row.get("net_income_eur"),
                "equity_eur": row.get("equity_eur"),
                "cash_eur": row.get("cash_eur"),
                "liabilities_eur": row.get("liabilities_eur"),
                "raw_json": raw_row,
                "normalized_json": {k: v for k, v in row.items() if k != "raw_json"},
                "openregister_company_id": best.get("company_id") if best else None,
                "match_status": status,
                "match_confidence": confidence,
                "match_method": method,
                "match_notes": None if status == "matched" else "Needs manual review" if status == "manual_review" else "No OpenRegister candidate found",
                "candidate_json": {"best": best, "candidates": candidates[:5]},
            }
            row_res = supabase.table("northdata_import_rows").insert(row_payload).execute()
            inserted_rows = getattr(row_res, "data", None) or []
            row_id = inserted_rows[0].get("id") if inserted_rows else None

            if status == "matched" and best:
                company_payload = build_company_payload(row, best, batch_id, row_id, confidence, method)
                supabase.table("companies").upsert(company_payload, on_conflict="openregister_company_id").execute()
                upsert_northdata_financials(supabase, row=row, company_payload=company_payload, batch_id=batch_id)
                supabase.table("northdata_import_rows").update({"imported_to_companies_at": now_iso()}).eq("id", row_id).execute()
                counters["matched"] += 1
                counters["companies_upserted"] += 1

                company_for_enrichment = {
                    "openregister_company_id": company_payload["openregister_company_id"],
                    "register_id": company_payload["register_id"],
                    "name": company_payload["name"],
                    "ownership_enriched_at": None,
                    "ubos_enriched_at": None,
                }
                if enrich_shareholders:
                    outcome = enrich_ownership(client, supabase, company_for_enrichment, update_existing=update_existing_enrichment, best_available=False)
                    counters["shareholders_enriched"] += 1 if outcome.get("status") in {"success", "skipped"} else 0
                if enrich_ubos_flag:
                    outcome = enrich_ubos(client, supabase, company_for_enrichment, update_existing=update_existing_enrichment)
                    counters["ubos_enriched"] += 1 if outcome.get("status") in {"success", "skipped"} else 0
            elif status == "manual_review":
                counters["manual_review"] += 1
            else:
                counters["unmatched"] += 1

            results.append({
                "row": i + 1,
                "company_name": row.get("company_name"),
                "register_court": row.get("register_court"),
                "register_id": row.get("register_id"),
                "parsed_type": row.get("register_type"),
                "parsed_number": row.get("register_number"),
                "match_status": status,
                "match_confidence": confidence,
                "match_method": method,
                "openregister_company_id": best.get("company_id") if best else None,
                "openregister_name": best.get("name") if best else None,
            })
        except Exception as exc:
            counters["errors"] += 1
            results.append({
                "row": i + 1,
                "company_name": row.get("company_name"),
                "register_id": row.get("register_id"),
                "match_status": "error",
                "error": str(exc),
            })

    update_import_batch(
        supabase,
        batch_id,
        matched_count=counters["matched"],
        manual_review_count=counters["manual_review"],
        unmatched_count=counters["unmatched"],
        error_count=counters["errors"],
        status="finished" if counters["errors"] == 0 else "finished_with_errors",
        finished_at=now_iso(),
    )
    return {"batch_id": batch_id, **counters, "results": results}


def northdata_integration_tab(supabase, openregister_api_key: str):
    st.header("NorthData Integration")
    st.caption("Upload NorthData company/financial data, match companies to OpenRegister by register court + register ID, then enrich shareholders/UBOs from OpenRegister.")

    uploaded_file = st.file_uploader("Upload NorthData CSV/XLSX", type=["csv", "xlsx", "xls"])
    if not uploaded_file:
        st.info("Upload a NorthData export to start.")
        return

    try:
        raw_df = read_uploaded_file(uploaded_file)
    except Exception as exc:
        st.error("Could not read uploaded file.")
        st.exception(exc)
        return

    if raw_df.empty:
        st.warning("The uploaded file is empty.")
        return

    st.success(f"Loaded {len(raw_df)} rows and {len(raw_df.columns)} columns from {uploaded_file.name}.")
    with st.expander("Uploaded columns", expanded=False):
        st.write(list(raw_df.columns))

    mapping = column_mapping_ui(raw_df)
    missing = validate_mapping(mapping)
    if missing:
        st.error("Map required fields before importing: " + ", ".join(missing))
        return

    normalized_df = normalize_northdata_dataframe(raw_df, mapping)

    st.subheader("Preview normalized data")
    st.dataframe(normalized_df.head(25), use_container_width=True)

    invalid_register_rows = normalized_df[normalized_df["register_type"].isna() | normalized_df["register_number"].isna()]
    if not invalid_register_rows.empty:
        st.warning(f"{len(invalid_register_rows)} rows do not have a parseable Register ID like 'HRB 12345'. They will fall back to name search or stay unmatched.")

    with st.form("northdata_import_run_form"):
        max_rows = st.number_input("Max rows to process now", min_value=1, max_value=int(len(normalized_df)), value=min(int(len(normalized_df)), 100), step=25)
        enrich_shareholders = st.checkbox("Enrich shareholders after matching", value=True)
        enrich_ubos_flag = st.checkbox("Enrich UBOs after matching", value=False)
        update_existing_enrichment = st.checkbox("Update existing shareholder/UBO enrichment", value=False)
        submitted = st.form_submit_button("Import NorthData + match OpenRegister", type="primary")

    if submitted:
        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return
        with st.spinner("Importing NorthData rows, matching OpenRegister IDs, and enriching selected data..."):
            result = process_northdata_import(
                supabase=supabase,
                api_key=openregister_api_key,
                file_name=uploaded_file.name,
                raw_df=raw_df,
                normalized_df=normalized_df,
                mapping=mapping,
                max_rows=int(max_rows),
                enrich_shareholders=enrich_shareholders,
                enrich_ubos_flag=enrich_ubos_flag,
                update_existing_enrichment=update_existing_enrichment,
            )
        st.success(
            f"NorthData import finished. Matched {result['matched']}, manual review {result['manual_review']}, unmatched {result['unmatched']}, errors {result['errors']}."
        )
        st.write("Import batch ID:", result.get("batch_id"))
        st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)
