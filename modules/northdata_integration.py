from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import pandas as pd
import streamlit as st

from modules.openregister_client import get_openregister_client
from modules.openregister_enrichment import enrich_ownership, enrich_ubos
from modules.utils import model_to_dict


NORTHDATA_TARGET_FIELDS = {
    "company_name": {
        "label": "Company Name",
        "required": True,
        "aliases": [
            "company name",
            "name",
            "firma",
            "unternehmen",
            "company",
            "source.name",
            "source name",
            "name of company",
            "company",
        ],
    },
    "register_court": {
        "label": "Register Court",
        "required": True,
        "aliases": [
            "register court",
            "registergericht",
            "amtsgericht",
            "court",
            "source.register court",
            "source register court",
        ],
    },
    "register_id": {
        "label": "Register ID",
        "required": True,
        "aliases": [
            "register id",
            "register no",
            "register number",
            "registernummer",
            "handelsregister",
            "hrb",
            "hra",
            "source.register id",
            "source register id",
        ],
    },
    "legal_form": {
        "label": "Legal Form",
        "required": False,
        "aliases": [
            "legal form",
            "rechtsform",
            "company type",
            "form",
            "source.legal form",
            "source legal form",
        ],
    },
    "city": {
        "label": "City",
        "required": False,
        "aliases": [
            "city",
            "ort",
            "stadt",
            "source.city",
            "source city",
        ],
    },
    "postal_code": {
        "label": "Postal Code",
        "required": False,
        "aliases": [
            "postal code",
            "postcode",
            "zip",
            "plz",
            "source.postal code",
            "source postal code",
        ],
    },
    "country_code": {
        "label": "Country Code",
        "required": False,
        "aliases": [
            "country code",
            "country",
            "land",
            "source.country code",
            "source country code",
        ],
    },
    "website": {
        "label": "Website",
        "required": False,
        "aliases": [
            "website",
            "url",
            "domain",
            "homepage",
            "web",
            "source.website",
            "source website",
        ],
    },
    "financials_date": {
        "label": "Financials Date",
        "required": False,
        "aliases": [
            "financials date",
            "fiscal year",
            "year",
            "latest financials",
            "abschlussdatum",
            "financial year",
        ],
    },
    "revenue_eur": {
        "label": "Revenue EUR",
        "required": False,
        "aliases": [
            "revenue",
            "revenue eur",
            "umsatz",
            "sales",
            "turnover",
            "erlöse",
            "revenues",
        ],
    },
    "employees": {
        "label": "Employees",
        "required": False,
        "aliases": [
            "employees",
            "employee count",
            "number of employees",
            "mitarbeiter",
            "beschäftigte",
            "staff",
        ],
    },
    "balance_sheet_total_eur": {
        "label": "Balance Sheet Total EUR",
        "required": False,
        "aliases": [
            "balance sheet total",
            "balance sheet total eur",
            "bilanzsumme",
            "total assets",
            "assets",
        ],
    },
    "net_income_eur": {
        "label": "Net Income EUR",
        "required": False,
        "aliases": [
            "net income",
            "net income eur",
            "profit",
            "earnings",
            "jahresüberschuss",
            "annual result",
            "net profit",
        ],
    },
    "equity_eur": {
        "label": "Equity EUR",
        "required": False,
        "aliases": [
            "equity",
            "equity eur",
            "eigenkapital",
        ],
    },
    "cash_eur": {
        "label": "Cash EUR",
        "required": False,
        "aliases": [
            "cash",
            "cash eur",
            "liquid funds",
            "liquide mittel",
        ],
    },
    "liabilities_eur": {
        "label": "Liabilities EUR",
        "required": False,
        "aliases": [
            "liabilities",
            "liabilities eur",
            "verbindlichkeiten",
            "debt",
        ],
    },
}


LEGAL_FORM_MAP = {
    "gmbh": "gmbh",
    "gesellschaft mit beschränkter haftung": "gmbh",
    "ug": "ug",
    "ug haftungsbeschränkt": "ug",
    "unternehmergesellschaft": "ug",
    "ggmbh": "ggmbh",
    "g gemeinnützige gmbh": "ggmbh",
    "ag": "ag",
    "aktiengesellschaft": "ag",
    "se": "se",
    "kgaa": "kgaa",
    "kg": "kg",
    "gmbh & co kg": "kg",
    "gmbh & co. kg": "kg",
    "ohg": "ohg",
    "ek": "ek",
    "e.k.": "ek",
    "eingetragener kaufmann": "ek",
    "eg": "eg",
    "e.g.": "eg",
    "ev": "ev",
    "e.v.": "ev",
    "gbr": "gbr",
    "egbr": "gbr",
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

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass

    return value


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ")
    text = text.replace("\u202f", " ")
    text = text.replace("\t", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_text(value: Any) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " und ")
    text = re.sub(r"[^a-z0-9äöüß]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_column_key(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9äöüß]+",
        "",
        clean_text(value).lower(),
    )


def normalize_company_name(value: Any) -> str:
    text = normalize_text(value)

    replacements = [
        "gesellschaft mit beschraenkter haftung",
        "gesellschaft mit beschränkter haftung",
        "gmbh",
        "ug haftungsbeschraenkt",
        "ug haftungsbeschränkt",
        "ug",
        "ag",
        "kg",
        "ohg",
        "gbr",
        "eg",
        "ev",
        "e v",
        "e k",
        "ek",
        "se",
        "kgaa",
    ]

    for rep in replacements:
        text = re.sub(rf"\b{re.escape(rep)}\b", " ", text)

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_legal_form(value: Any) -> str | None:
    text = normalize_text(value)

    if not text:
        return None

    compact = text.replace(" ", "")

    direct_candidates = [
        text,
        compact,
        text.replace(" und ", " & "),
        text.replace(" & ", " und "),
    ]

    for candidate in direct_candidates:
        if candidate in LEGAL_FORM_MAP:
            return LEGAL_FORM_MAP[candidate]

    if "ggmbh" in compact:
        return "ggmbh"

    if "gmbh" in compact and "co" in compact and "kg" in compact:
        return "kg"

    if "gmbh" in compact:
        return "gmbh"

    if compact.startswith("ug") or "unternehmergesellschaft" in text:
        return "ug"

    if compact == "ag" or "aktiengesellschaft" in text:
        return "ag"

    if compact == "se":
        return "se"

    if "kgaa" in compact:
        return "kgaa"

    if compact == "kg":
        return "kg"

    if compact == "ohg":
        return "ohg"

    if compact in {"ek", "e.k."} or "eingetragenerkaufmann" in compact:
        return "ek"

    if compact in {"eg", "e.g."}:
        return "eg"

    if compact in {"ev", "e.v."}:
        return "ev"

    if compact in {"gbr", "egbr"}:
        return "gbr"

    if compact == "ewiv":
        return "ewiv"

    if compact == "llp":
        return "llp"

    return text


def infer_register_type_from_legal_form(legal_form: Any) -> str | None:
    form = normalize_legal_form(legal_form)

    if not form:
        return None

    if form in {"gmbh", "ug", "ggmbh", "ag", "se", "kgaa"}:
        return "HRB"

    if form in {"kg", "ohg", "ek"}:
        return "HRA"

    if form == "eg":
        return "GnR"

    if form == "ev":
        return "VR"

    return None


def parse_register_id(value: Any, legal_form: Any = None) -> tuple[str | None, str | None]:
    """
    Parses NorthData Register ID values.

    Examples:
    HRB 700077       -> ("HRB", "700077")
    HRB700077        -> ("HRB", "700077")
    HRB-700077       -> ("HRB", "700077")
    Amtsgericht X HRB 700077 -> ("HRB", "700077")

    Fallback:
    If the HRB/HRA text cannot be parsed because of weird file characters,
    extract the first number and infer register type from legal form.
    """
    inferred_type = infer_register_type_from_legal_form(legal_form)

    if value is None:
        return inferred_type, None

    text = clean_text(value)

    if not text:
        return inferred_type, None

    text_upper = text.upper()
    text_upper = unicodedata.normalize("NFKC", text_upper)
    text_upper = text_upper.replace("\u00a0", " ")
    text_upper = text_upper.replace("\u202f", " ")
    text_upper = re.sub(r"\s+", " ", text_upper).strip()

    match = re.search(
        r"(HRB|HRA|VR|GNR|PR)\s*[-/:.]?\s*([0-9][0-9A-Z./-]*)",
        text_upper,
    )

    if match:
        register_type = match.group(1)
        register_number = match.group(2).strip()

        if register_type == "GNR":
            register_type = "GnR"

        return register_type, register_number

    number_match = re.search(r"([0-9][0-9A-Z./-]*)", text_upper)
    register_number = number_match.group(1).strip() if number_match else None

    return inferred_type, register_number


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
        .replace("\u202f", "")
        .replace(" ", "")
        .strip()
    )

    text = re.sub(r"[^0-9,.\-]", "", text)

    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")

    elif "," in text:
        parts = text.split(",")

        if len(parts) > 2:
            text = "".join(parts)
        else:
            before, after = parts

            if len(after) == 3 and before.replace("-", "").isdigit():
                text = before + after
            else:
                text = before + "." + after

    elif "." in text:
        parts = text.split(".")

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

    try:
        return int(round(number))
    except Exception:
        return None


def guess_column(columns: list[str], aliases: list[str]) -> str | None:
    normalized_columns = {
        normalize_column_key(col): col
        for col in columns
    }

    for alias in aliases:
        alias_key = normalize_column_key(alias)

        if alias_key in normalized_columns:
            return normalized_columns[alias_key]

    for alias in aliases:
        alias_key = normalize_column_key(alias)

        for col_key, original_col in normalized_columns.items():
            if alias_key and (alias_key in col_key or col_key in alias_key):
                return original_col

    return None


def resolve_typed_column_name(typed_value: str | None, columns: list[str]) -> str | None:
    typed = clean_text(typed_value)

    if not typed:
        return None

    typed_key = normalize_column_key(typed)

    if not typed_key:
        return None

    normalized_map = {
        normalize_column_key(col): col
        for col in columns
    }

    if typed_key in normalized_map:
        return normalized_map[typed_key]

    possible_matches = [
        original_col
        for col_key, original_col in normalized_map.items()
        if typed_key in col_key or col_key in typed_key
    ]

    if len(possible_matches) == 1:
        return possible_matches[0]

    return None


def read_uploaded_file(uploaded_file) -> pd.DataFrame:
    file_name = uploaded_file.name.lower()

    if file_name.endswith(".csv"):
        return pd.read_csv(uploaded_file, sep=None, engine="python")

    return pd.read_excel(uploaded_file)


def column_mapping_ui(df: pd.DataFrame) -> dict[str, str | None]:
    columns = list(df.columns)
    mapping: dict[str, str | None] = {}

    st.subheader("Column mapping")
    st.caption(
        "Type the uploaded NorthData column name for each internal field. "
        "The app pre-fills guesses. You can manually edit them. "
        "Leave optional fields blank to ignore them."
    )

    with st.expander("Uploaded file columns", expanded=False):
        st.write(columns)

    for target_field, config in NORTHDATA_TARGET_FIELDS.items():
        guessed = guess_column(columns, config["aliases"]) or ""

        typed_value = st.text_input(
            config["label"] + (" *" if config["required"] else ""),
            value=guessed,
            placeholder="Type exact uploaded column name",
            key=f"northdata_map_{target_field}",
        )

        resolved_column = resolve_typed_column_name(typed_value, columns)
        mapping[target_field] = resolved_column

        if clean_text(typed_value) and not resolved_column:
            st.warning(
                f"Could not find uploaded column '{typed_value}' for {config['label']}."
            )

        elif resolved_column and clean_text(typed_value) != resolved_column:
            st.caption(f"Using uploaded column: `{resolved_column}`")

    return mapping


def validate_mapping(mapping: dict[str, str | None]) -> list[str]:
    errors: list[str] = []

    for target_field, config in NORTHDATA_TARGET_FIELDS.items():
        if config["required"] and not mapping.get(target_field):
            errors.append(f"Missing required mapping: {config['label']}")

    return errors


def normalize_northdata_dataframe(df: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame:
    normalized = pd.DataFrame(index=df.index)

    for target_field in NORTHDATA_TARGET_FIELDS.keys():
        source_col = mapping.get(target_field)

        if source_col and source_col in df.columns:
            normalized[target_field] = df[source_col]
        else:
            normalized[target_field] = None

    text_fields = [
        "company_name",
        "register_court",
        "register_id",
        "city",
        "postal_code",
        "country_code",
        "website",
        "financials_date",
    ]

    for field in text_fields:
        normalized[field] = normalized[field].map(clean_text)

    normalized["legal_form"] = normalized["legal_form"].map(normalize_legal_form)

    parsed = normalized.apply(
        lambda row: parse_register_id(
            row.get("register_id"),
            row.get("legal_form"),
        ),
        axis=1,
    )

    normalized["register_type"] = parsed.map(lambda x: x[0])
    normalized["register_number"] = parsed.map(lambda x: x[1])

    money_fields = [
        "revenue_eur",
        "balance_sheet_total_eur",
        "net_income_eur",
        "equity_eur",
        "cash_eur",
        "liabilities_eur",
    ]

    for field in money_fields:
        normalized[field] = normalized[field].map(parse_number)

    normalized["employees"] = normalized["employees"].map(parse_int)

    normalized["source_system"] = "northdata_upload"

    return normalized


def similarity(a: Any, b: Any) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)

    if not a_norm or not b_norm:
        return 0.0

    return SequenceMatcher(None, a_norm, b_norm).ratio()


def get_search_results(response: Any) -> list[dict[str, Any]]:
    data = model_to_dict(response)
    results = data.get("results") or []

    if not isinstance(results, list):
        return []

    return [model_to_dict(item) for item in results]


def openregister_search(client, filters: list[dict[str, Any]], query: str | None = None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "filters": filters,
        "pagination": {
            "page": 1,
            "per_page": 10,
        },
    }

    if query:
        payload["query"] = query

    try:
        response = client.search.find_companies_v1(**payload)
        return get_search_results(response)
    except Exception:
        return []


def search_openregister_candidates(
    client,
    *,
    company_name: str | None,
    register_court: str | None,
    register_type: str | None,
    register_number: str | None,
) -> tuple[list[dict[str, Any]], str]:
    candidates: list[dict[str, Any]] = []

    if register_court and register_type and register_number:
        filters = [
            {"field": "register_court", "value": register_court},
            {"field": "register_type", "value": register_type},
            {"field": "register_number", "value": register_number},
        ]

        candidates = openregister_search(client, filters=filters)

        if candidates:
            return candidates, "exact_register_court_type_number"

    if register_type and register_number:
        filters = [
            {"field": "register_type", "value": register_type},
            {"field": "register_number", "value": register_number},
        ]

        candidates = openregister_search(
            client,
            filters=filters,
            query=company_name or None,
        )

        if candidates:
            return candidates, "register_type_number"

    if company_name:
        candidates = openregister_search(
            client,
            filters=[],
            query=company_name,
        )

        if candidates:
            return candidates, "company_name_search"

    return [], "no_candidates"


def score_candidate(
    row: pd.Series,
    candidate: dict[str, Any],
    method: str,
) -> float:
    score = 0.0

    row_type = clean_text(row.get("register_type"))
    row_number = clean_text(row.get("register_number"))
    row_court = clean_text(row.get("register_court"))
    row_name = clean_text(row.get("company_name"))
    row_city = clean_text(row.get("city"))
    row_legal_form = clean_text(row.get("legal_form"))

    cand_type = clean_text(candidate.get("register_type"))
    cand_number = clean_text(candidate.get("register_number"))
    cand_court = clean_text(candidate.get("register_court"))
    cand_name = clean_text(candidate.get("name"))
    cand_city = clean_text(candidate.get("city"))
    cand_legal_form = clean_text(candidate.get("legal_form"))

    if row_type and cand_type and row_type.upper() == cand_type.upper():
        score += 25

    if row_number and cand_number and row_number.upper() == cand_number.upper():
        score += 25

    if row_court and cand_court:
        if normalize_text(row_court) == normalize_text(cand_court):
            score += 25
        elif normalize_text(row_court) in normalize_text(cand_court) or normalize_text(cand_court) in normalize_text(row_court):
            score += 18
        else:
            score += similarity(row_court, cand_court) * 10

    if row_name and cand_name:
        row_name_norm = normalize_company_name(row_name)
        cand_name_norm = normalize_company_name(cand_name)

        if row_name_norm and cand_name_norm and row_name_norm == cand_name_norm:
            score += 15
        else:
            score += similarity(row_name_norm, cand_name_norm) * 15

    if row_city and cand_city:
        if normalize_text(row_city) == normalize_text(cand_city):
            score += 5
        else:
            score += similarity(row_city, cand_city) * 3

    if row_legal_form and cand_legal_form and normalize_legal_form(row_legal_form) == normalize_legal_form(cand_legal_form):
        score += 5

    if method == "exact_register_court_type_number":
        score = max(score, 95)

    return round(min(score, 100), 2)


def choose_best_match(
    row: pd.Series,
    candidates: list[dict[str, Any]],
    method: str,
) -> tuple[dict[str, Any] | None, str, float, str]:
    if not candidates:
        return None, "unmatched", 0.0, method

    scored = [
        (candidate, score_candidate(row, candidate, method))
        for candidate in candidates
    ]

    scored.sort(key=lambda x: x[1], reverse=True)

    best_candidate, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0

    if best_score >= 95 and len(scored) == 1:
        return best_candidate, "matched", best_score, method

    if best_score >= 85 and (best_score - second_score) >= 5:
        return best_candidate, "matched", best_score, method

    return best_candidate, "manual_review", best_score, method


def create_import_batch(
    supabase,
    *,
    file_name: str,
    row_count: int,
    column_mapping: dict[str, str | None],
) -> str:
    payload = {
        "file_name": file_name,
        "row_count": row_count,
        "column_mapping_json": json_safe(column_mapping),
        "status": "running",
    }

    result = supabase.table("northdata_import_batches").insert(payload).execute()
    return result.data[0]["id"]


def update_import_batch(
    supabase,
    batch_id: str,
    *,
    matched_count: int,
    manual_review_count: int,
    unmatched_count: int,
    error_count: int,
) -> None:
    status = "finished"

    if error_count:
        status = "finished_with_errors"

    supabase.table("northdata_import_batches").update(
        {
            "matched_count": matched_count,
            "manual_review_count": manual_review_count,
            "unmatched_count": unmatched_count,
            "error_count": error_count,
            "status": status,
            "finished_at": now_iso(),
        }
    ).eq("id", batch_id).execute()


def build_company_payload(
    row: pd.Series,
    candidate: dict[str, Any],
    *,
    batch_id: str,
    row_id: str,
    confidence: float,
    match_method: str,
) -> dict[str, Any]:
    openregister_company_id = clean_text(candidate.get("company_id"))

    register_id = openregister_company_id

    financial_values = [
        row.get("revenue_eur"),
        row.get("employees"),
        row.get("balance_sheet_total_eur"),
        row.get("net_income_eur"),
        row.get("equity_eur"),
        row.get("cash_eur"),
        row.get("liabilities_eur"),
    ]

    has_financials = any(value is not None for value in financial_values)

    payload = {
        "openregister_company_id": openregister_company_id,
        "register_id": register_id,

        "name": clean_text(row.get("company_name")) or clean_text(candidate.get("name")),
        "legal_form": clean_text(row.get("legal_form")) or clean_text(candidate.get("legal_form")),
        "active": candidate.get("active"),
        "country": clean_text(row.get("country_code")) or clean_text(candidate.get("country")),
        "register_number": clean_text(row.get("register_number")) or clean_text(candidate.get("register_number")),
        "register_court": clean_text(row.get("register_court")) or clean_text(candidate.get("register_court")),
        "register_type": clean_text(row.get("register_type")) or clean_text(candidate.get("register_type")),

        "city": clean_text(row.get("city")) or clean_text(candidate.get("city")),
        "postal_code": clean_text(row.get("postal_code")),
        "website": clean_text(row.get("website")),

        "financials_date": clean_text(row.get("financials_date")),
        "revenue_eur": row.get("revenue_eur"),
        "employees": row.get("employees"),
        "balance_sheet_total_eur": row.get("balance_sheet_total_eur"),
        "net_income_eur": row.get("net_income_eur"),
        "equity_eur": row.get("equity_eur"),
        "cash_eur": row.get("cash_eur"),
        "liabilities_eur": row.get("liabilities_eur"),

        "source": "northdata_upload",
        "company_data_source": "northdata",
        "financial_data_source": "northdata",

        "northdata_import_batch_id": batch_id,
        "northdata_import_row_id": row_id,
        "northdata_raw_data": json_safe(row.to_dict()),
        "northdata_match_status": "matched",
        "northdata_match_confidence": confidence,
        "northdata_match_method": match_method,

        "financials_enriched_at": now_iso() if has_financials else None,
        "raw_search_result": json_safe(candidate),
        "updated_at": now_iso(),
    }

    return payload


def upsert_northdata_financials(
    supabase,
    *,
    row: pd.Series,
    candidate: dict[str, Any],
    batch_id: str,
) -> None:
    openregister_company_id = clean_text(candidate.get("company_id"))

    if not openregister_company_id:
        return

    financial_values = [
        row.get("revenue_eur"),
        row.get("employees"),
        row.get("balance_sheet_total_eur"),
        row.get("net_income_eur"),
        row.get("equity_eur"),
        row.get("cash_eur"),
        row.get("liabilities_eur"),
    ]

    if not any(value is not None for value in financial_values):
        return

    payload = {
        "company_register_id": openregister_company_id,
        "openregister_company_id": openregister_company_id,
        "company_name": clean_text(row.get("company_name")) or clean_text(candidate.get("name")),
        "latest_report_end_date": clean_text(row.get("financials_date")),
        "source_system": "northdata",
        "source_import_batch_id": batch_id,
        "source_raw_data": json_safe(row.to_dict()),
        "raw_financials": {
            "source": "northdata_upload",
            "financials_date": clean_text(row.get("financials_date")),
            "revenue_eur": row.get("revenue_eur"),
            "employees": row.get("employees"),
            "balance_sheet_total_eur": row.get("balance_sheet_total_eur"),
            "net_income_eur": row.get("net_income_eur"),
            "equity_eur": row.get("equity_eur"),
            "cash_eur": row.get("cash_eur"),
            "liabilities_eur": row.get("liabilities_eur"),
        },
        "api_status": "success",
        "notes": "Imported from NorthData upload.",
        "updated_at": now_iso(),
    }

    supabase.table("company_financials").upsert(
        payload,
        on_conflict="openregister_company_id",
    ).execute()


def company_for_enrichment(candidate: dict[str, Any]) -> dict[str, Any]:
    openregister_company_id = clean_text(candidate.get("company_id"))

    return {
        "register_id": openregister_company_id,
        "openregister_company_id": openregister_company_id,
        "name": clean_text(candidate.get("name")),
        "ownership_enriched_at": None,
        "ubos_enriched_at": None,
    }


def process_northdata_import(
    *,
    supabase,
    api_key: str,
    df: pd.DataFrame,
    normalized_df: pd.DataFrame,
    file_name: str,
    column_mapping: dict[str, str | None],
    max_rows: int,
    enrich_shareholders: bool,
    enrich_ubos_flag: bool,
    update_existing_enrichment: bool,
) -> dict[str, Any]:
    client = get_openregister_client(api_key)

    total_rows = min(len(normalized_df), max_rows)

    batch_id = create_import_batch(
        supabase,
        file_name=file_name,
        row_count=total_rows,
        column_mapping=column_mapping,
    )

    matched_count = 0
    manual_review_count = 0
    unmatched_count = 0
    error_count = 0

    results: list[dict[str, Any]] = []

    for idx, row in normalized_df.head(total_rows).iterrows():
        row_number = int(idx) + 2

        try:
            raw_row_json = json_safe(df.loc[idx].to_dict())
            normalized_json = json_safe(row.to_dict())

            company_name = clean_text(row.get("company_name"))
            register_court = clean_text(row.get("register_court"))
            register_id = clean_text(row.get("register_id"))
            register_type = clean_text(row.get("register_type"))
            register_number = clean_text(row.get("register_number"))

            if not register_type or not register_number:
                row_payload = {
                    "batch_id": batch_id,
                    "row_number": row_number,
                    "company_name": company_name,
                    "register_court": register_court,
                    "register_id": register_id,
                    "register_type": register_type,
                    "register_number": register_number,
                    "legal_form": clean_text(row.get("legal_form")),
                    "city": clean_text(row.get("city")),
                    "postal_code": clean_text(row.get("postal_code")),
                    "country_code": clean_text(row.get("country_code")),
                    "website": clean_text(row.get("website")),
                    "financials_date": clean_text(row.get("financials_date")),
                    "revenue_eur": row.get("revenue_eur"),
                    "employees": row.get("employees"),
                    "balance_sheet_total_eur": row.get("balance_sheet_total_eur"),
                    "net_income_eur": row.get("net_income_eur"),
                    "equity_eur": row.get("equity_eur"),
                    "cash_eur": row.get("cash_eur"),
                    "liabilities_eur": row.get("liabilities_eur"),
                    "raw_json": raw_row_json,
                    "normalized_json": normalized_json,
                    "match_status": "error",
                    "match_confidence": 0,
                    "match_method": "parse_register_id",
                    "match_notes": "Could not parse register type/number from Register ID.",
                }

                insert_result = supabase.table("northdata_import_rows").insert(row_payload).execute()
                row_id = insert_result.data[0]["id"]

                error_count += 1

                results.append(
                    {
                        "row_number": row_number,
                        "company_name": company_name,
                        "register_court": register_court,
                        "register_id": register_id,
                        "parsed_type": register_type,
                        "parsed_number": register_number,
                        "match_status": "error",
                        "confidence": 0,
                        "method": "parse_register_id",
                        "openregister_company_id": None,
                        "openregister_name": None,
                        "northdata_import_row_id": row_id,
                    }
                )

                continue

            candidates, method = search_openregister_candidates(
                client,
                company_name=company_name,
                register_court=register_court,
                register_type=register_type,
                register_number=register_number,
            )

            best_candidate, match_status, confidence, match_method = choose_best_match(
                row,
                candidates,
                method,
            )

            if match_status == "matched":
                matched_count += 1
            elif match_status == "manual_review":
                manual_review_count += 1
            else:
                unmatched_count += 1

            row_payload = {
                "batch_id": batch_id,
                "row_number": row_number,
                "company_name": company_name,
                "register_court": register_court,
                "register_id": register_id,
                "register_type": register_type,
                "register_number": register_number,
                "legal_form": clean_text(row.get("legal_form")),
                "city": clean_text(row.get("city")),
                "postal_code": clean_text(row.get("postal_code")),
                "country_code": clean_text(row.get("country_code")),
                "website": clean_text(row.get("website")),
                "financials_date": clean_text(row.get("financials_date")),
                "revenue_eur": row.get("revenue_eur"),
                "employees": row.get("employees"),
                "balance_sheet_total_eur": row.get("balance_sheet_total_eur"),
                "net_income_eur": row.get("net_income_eur"),
                "equity_eur": row.get("equity_eur"),
                "cash_eur": row.get("cash_eur"),
                "liabilities_eur": row.get("liabilities_eur"),
                "raw_json": raw_row_json,
                "normalized_json": normalized_json,
                "openregister_company_id": clean_text(best_candidate.get("company_id")) if best_candidate else None,
                "match_status": match_status,
                "match_confidence": confidence,
                "match_method": match_method,
                "candidate_json": json_safe(best_candidate) if best_candidate else None,
            }

            insert_result = supabase.table("northdata_import_rows").insert(row_payload).execute()
            row_id = insert_result.data[0]["id"]

            if match_status == "matched" and best_candidate:
                company_payload = build_company_payload(
                    row,
                    best_candidate,
                    batch_id=batch_id,
                    row_id=row_id,
                    confidence=confidence,
                    match_method=match_method,
                )

                supabase.table("companies").upsert(
                    company_payload,
                    on_conflict="openregister_company_id",
                ).execute()

                upsert_northdata_financials(
                    supabase,
                    row=row,
                    candidate=best_candidate,
                    batch_id=batch_id,
                )

                supabase.table("northdata_import_rows").update(
                    {
                        "imported_to_companies_at": now_iso(),
                    }
                ).eq("id", row_id).execute()

                enrichment_company = company_for_enrichment(best_candidate)

                if enrich_shareholders:
                    enrich_ownership(
                        client=client,
                        supabase=supabase,
                        company=enrichment_company,
                        update_existing=update_existing_enrichment,
                    )

                if enrich_ubos_flag:
                    enrich_ubos(
                        client=client,
                        supabase=supabase,
                        company=enrichment_company,
                        update_existing=update_existing_enrichment,
                    )

            results.append(
                {
                    "row_number": row_number,
                    "company_name": company_name,
                    "register_court": register_court,
                    "register_id": register_id,
                    "parsed_type": register_type,
                    "parsed_number": register_number,
                    "match_status": match_status,
                    "confidence": confidence,
                    "method": match_method,
                    "openregister_company_id": clean_text(best_candidate.get("company_id")) if best_candidate else None,
                    "openregister_name": clean_text(best_candidate.get("name")) if best_candidate else None,
                    "northdata_import_row_id": row_id,
                }
            )

        except Exception as exc:
            error_count += 1

            results.append(
                {
                    "row_number": row_number,
                    "company_name": clean_text(row.get("company_name")),
                    "register_court": clean_text(row.get("register_court")),
                    "register_id": clean_text(row.get("register_id")),
                    "parsed_type": clean_text(row.get("register_type")),
                    "parsed_number": clean_text(row.get("register_number")),
                    "match_status": "error",
                    "confidence": 0,
                    "method": "exception",
                    "openregister_company_id": None,
                    "openregister_name": None,
                    "error": str(exc),
                }
            )

    update_import_batch(
        supabase,
        batch_id,
        matched_count=matched_count,
        manual_review_count=manual_review_count,
        unmatched_count=unmatched_count,
        error_count=error_count,
    )

    return {
        "batch_id": batch_id,
        "processed": total_rows,
        "matched": matched_count,
        "manual_review": manual_review_count,
        "unmatched": unmatched_count,
        "errors": error_count,
        "results": results,
    }


def northdata_integration_tab(
    supabase,
    openregister_api_key: str,
) -> None:
    st.header("NorthData Integration")
    st.caption(
        "Upload a NorthData company/financial file, map its columns manually, "
        "match each company to OpenRegister using Register Court + Register ID, "
        "then enrich shareholders and UBOs from OpenRegister."
    )

    uploaded_file = st.file_uploader(
        "Upload NorthData CSV/XLSX",
        type=["csv", "xlsx", "xls"],
    )

    if not uploaded_file:
        st.info("Upload a NorthData export file to start.")
        return

    try:
        df = read_uploaded_file(uploaded_file)
    except Exception as exc:
        st.error("Could not read uploaded file.")
        st.exception(exc)
        return

    if df.empty:
        st.warning("Uploaded file is empty.")
        return

    st.success(f"Loaded {len(df)} rows and {len(df.columns)} columns.")

    with st.expander("Raw uploaded preview", expanded=False):
        st.dataframe(df.head(20), use_container_width=True)

    mapping = column_mapping_ui(df)
    mapping_errors = validate_mapping(mapping)

    if mapping_errors:
        st.error("Fix these required column mappings first:")
        for error in mapping_errors:
            st.write(f"- {error}")
        return

    normalized_df = normalize_northdata_dataframe(df, mapping)

    st.subheader("Normalized preview")
    st.dataframe(normalized_df.head(30), use_container_width=True)

    invalid_register_rows = normalized_df[
        normalized_df["register_type"].isna()
        | normalized_df["register_number"].isna()
        | (normalized_df["register_type"].astype(str).str.strip() == "")
        | (normalized_df["register_number"].astype(str).str.strip() == "")
    ]

    if not invalid_register_rows.empty:
        st.warning(
            f"{len(invalid_register_rows)} rows have Register ID parsing problems. "
            "They will be stored as errors and not matched until fixed."
        )

        with st.expander("Rows with Register ID parse problems", expanded=False):
            cols = [
                "company_name",
                "register_court",
                "register_id",
                "legal_form",
                "register_type",
                "register_number",
            ]
            st.dataframe(invalid_register_rows[cols].head(50), use_container_width=True)

    with st.form("northdata_import_form"):
        max_rows = st.number_input(
            "Max rows to process",
            min_value=1,
            max_value=len(normalized_df),
            value=min(len(normalized_df), 100),
            step=10,
        )

        enrich_shareholders = st.checkbox(
            "Enrich shareholders from OpenRegister after match",
            value=True,
        )

        enrich_ubos_flag = st.checkbox(
            "Enrich UBO/control chain from OpenRegister after match",
            value=False,
        )

        update_existing_enrichment = st.checkbox(
            "Update existing shareholder/UBO enrichment",
            value=False,
        )

        submitted = st.form_submit_button(
            "Import NorthData + match OpenRegister",
            type="primary",
        )

    if submitted:
        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return

        with st.spinner("Importing NorthData rows and matching OpenRegister companies..."):
            result = process_northdata_import(
                supabase=supabase,
                api_key=openregister_api_key,
                df=df,
                normalized_df=normalized_df,
                file_name=uploaded_file.name,
                column_mapping=mapping,
                max_rows=int(max_rows),
                enrich_shareholders=enrich_shareholders,
                enrich_ubos_flag=enrich_ubos_flag,
                update_existing_enrichment=update_existing_enrichment,
            )

        st.success(
            f"NorthData import finished. "
            f"Processed {result['processed']}, "
            f"matched {result['matched']}, "
            f"manual review {result['manual_review']}, "
            f"unmatched {result['unmatched']}, "
            f"errors {result['errors']}."
        )

        st.write(f"Import batch ID: `{result['batch_id']}`")

        if result["results"]:
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)
