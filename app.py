from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.claude_business_model import run_claude_business_model_enrichment
from modules.filtered_workbook_export import (
    apply_numeric_filter,
    build_filtered_workbook_bytes,
    fetch_all_rows_paginated,
)
from modules.fit_scoring import DEFAULT_FIT_CONFIG, run_fit_scoring
from modules.google_sheets_sync import sync_supabase_to_google_sheets
from modules.northdata_import import run_northdata_import
from modules.openregister_enrichment import run_enrichment
from modules.openregister_search import run_company_search, validate_filter_config
from modules.supabase_client import get_supabase_client
from modules.utils import parse_csv_values


st.set_page_config(
    page_title="CoK Nachfolge Pipeline",
    layout="wide",
)


LEGAL_FORM_OPTIONS = {
    "GmbH": "gmbh",
    "UG": "ug",
    "GmbH & Co. KG / KG": "kg",
    "OHG": "ohg",
    "e.K.": "ek",
}

DEFAULT_LEGAL_FORMS = ["gmbh", "ug", "kg", "ohg", "ek"]

FINANCIAL_FIELDS = [
    ("Revenue EUR", "revenue_eur"),
    ("Employees", "employees"),
    ("Balance sheet total EUR", "balance_sheet_total_eur"),
    ("Net income EUR", "net_income_eur"),
    ("Equity EUR", "equity_eur"),
    ("Cash EUR", "cash_eur"),
    ("Liabilities EUR", "liabilities_eur"),
    ("Real estate EUR", "real_estate_eur"),
    ("Capital amount EUR", "capital_amount_eur"),
]


def bool_filter(label: str, key: str):
    value = st.selectbox(label, ["Any", "Yes", "No"], key=key)
    if value == "Any":
        return None
    return value == "Yes"


def optional_int_input(label: str, key: str):
    value = st.text_input(label, key=key, placeholder="Leave blank")
    if not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        st.warning(f"{label} must be an integer.")
        return None


def optional_float_input(label: str, key: str):
    value = st.text_input(label, key=key, placeholder="Leave blank")
    if not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        st.warning(f"{label} must be a number.")
        return None


def financial_range_inputs(prefix: str):
    filters = {}
    for label, field in FINANCIAL_FIELDS:
        with st.expander(label, expanded=False):
            min_value = optional_float_input(f"Min {label}", key=f"{prefix}_{field}_min")
            max_value = optional_float_input(f"Max {label}", key=f"{prefix}_{field}_max")
            if min_value is not None or max_value is not None:
                filters[field] = {"min": min_value, "max": max_value}
    return filters


def search_tab(supabase, openregister_api_key: str):
    st.header("Filter Search")

    with st.form("openregister_search_form"):
        col1, col2, col3 = st.columns(3)

        with col1:
            legal_forms = st.multiselect(
                "Legal forms",
                options=list(LEGAL_FORM_OPTIONS.keys()),
                default=list(LEGAL_FORM_OPTIONS.keys()),
            )
            active = bool_filter("Active", "search_active")
            countries = parse_csv_values(
                st.text_input("Countries", value="DE", help="Comma-separated country codes.")
            )
            register_types = parse_csv_values(
                st.text_input("Register types", value="", help="Example: HRB,HRA")
            )

        with col2:
            register_courts = parse_csv_values(
                st.text_input("Register courts", value="", help="Comma-separated.")
            )
            cities = parse_csv_values(st.text_input("Cities", value=""))
            postal_codes = parse_csv_values(st.text_input("Postal codes", value=""))
            industry_codes = parse_csv_values(
                st.text_input("Industry codes", value="", help="Comma-separated.")
            )

        with col3:
            purpose_keywords = parse_csv_values(
                st.text_area("Purpose keywords", value="", help="Comma-separated keywords.")
            )
            limit = st.number_input("Maximum results", min_value=1, max_value=500, value=50, step=10)
            page_size = st.number_input("Page size", min_value=10, max_value=100, value=50, step=10)

        st.subheader("Financial filters")
        financial_filters = financial_range_inputs("search_financial")

        st.subheader("Ownership / Succession filters")
        owner_filters = {
            "min_number_of_owners": optional_int_input("Min number of owners", "min_number_of_owners"),
            "max_number_of_owners": optional_int_input("Max number of owners", "max_number_of_owners"),
            "min_natural_person_owner_count": optional_int_input(
                "Min natural person owners", "min_natural_person_owner_count"
            ),
            "max_natural_person_owner_count": optional_int_input(
                "Max natural person owners", "max_natural_person_owner_count"
            ),
            "min_youngest_owner_age": optional_int_input("Min youngest owner age", "min_youngest_owner_age"),
            "max_youngest_owner_age": optional_int_input("Max youngest owner age", "max_youngest_owner_age"),
            "min_oldest_owner_age": optional_int_input("Min oldest owner age", "min_oldest_owner_age"),
            "max_oldest_owner_age": optional_int_input("Max oldest owner age", "max_oldest_owner_age"),
            "has_sole_owner": bool_filter("Has sole owner", "has_sole_owner"),
            "is_family_owned": bool_filter("Is family owned", "is_family_owned"),
            "has_majority_owner": bool_filter("Has majority owner", "has_majority_owner"),
        }

        submitted = st.form_submit_button("Run OpenRegister Search", type="primary")

    if submitted:
        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return

        selected_legal_forms = [LEGAL_FORM_OPTIONS[item] for item in legal_forms]

        filter_config = {
            "legal_forms": selected_legal_forms,
            "active": active,
            "countries": countries,
            "register_types": register_types,
            "register_courts": register_courts,
            "cities": cities,
            "postal_codes": postal_codes,
            "industry_codes": industry_codes,
            "purpose_keywords": purpose_keywords,
            "financial_filters": financial_filters,
            "owner_filters": {k: v for k, v in owner_filters.items() if v is not None},
        }

        warnings = validate_filter_config(filter_config)
        for warning in warnings:
            st.warning(warning)

        with st.spinner("Searching OpenRegister and saving companies..."):
            result = run_company_search(
                openregister_api_key=openregister_api_key,
                supabase=supabase,
                filter_config=filter_config,
                limit=int(limit),
                page_size=int(page_size),
            )

        st.success(f"Saved or updated {result['saved_count']} companies.")
        if result.get("errors"):
            st.warning(f"{len(result['errors'])} rows had errors.")
            st.dataframe(pd.DataFrame(result["errors"]))

        if result.get("companies"):
            st.dataframe(pd.DataFrame(result["companies"]), use_container_width=True)


def northdata_import_tab(supabase, openregister_api_key: str):
    st.header("NorthData Import")
    st.caption(
        "Upload a NorthData Excel file. Each row is matched to OpenRegister first. "
        "Only matched companies are inserted or updated using the real OpenRegister company ID."
    )

    uploaded_file = st.file_uploader(
        "Upload NorthData Excel file",
        type=["xlsx"],
        help="Only .xlsx is supported.",
    )

    max_rows = st.number_input(
        "Max rows to process",
        min_value=0,
        value=0,
        step=10,
        help="Use 0 to process all rows. Use a small number for testing first.",
    )

    if uploaded_file is not None:
        try:
            uploaded_file.seek(0)
            preview_df = pd.read_excel(uploaded_file, engine="openpyxl").head(20)
            uploaded_file.seek(0)

            st.subheader("Preview")
            st.dataframe(preview_df, use_container_width=True)

        except Exception as exc:
            st.error(f"Could not read Excel file: {exc}")
            return

    if st.button("Import NorthData and match OpenRegister", type="primary"):
        if uploaded_file is None:
            st.error("Upload a NorthData Excel file first.")
            return

        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return

        uploaded_file.seek(0)

        with st.spinner("Importing NorthData rows and matching OpenRegister IDs..."):
            result = run_northdata_import(
                uploaded_file=uploaded_file,
                openregister_api_key=openregister_api_key,
                supabase=supabase,
                max_rows=int(max_rows) if max_rows and max_rows > 0 else None,
            )

        st.success(
            f"NorthData import finished. "
            f"Imported {result['imported']}, updated {result['updated']}, "
            f"skipped {result['skipped']}, errors {result['errors']}."
        )

        st.dataframe(
            pd.DataFrame([{
                "Total rows": result["total_rows"],
                "Imported new": result["imported"],
                "Updated existing": result["updated"],
                "Skipped": result["skipped"],
                "Errors": result["errors"],
            }]),
            use_container_width=True,
        )

        if result.get("results"):
            st.subheader("Row results")
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)


def enrichment_tab(supabase, openregister_api_key: str):
    st.header("OpenRegister Enrichment")

    col1, col2, col3 = st.columns(3)
    with col1:
        limit = st.number_input("Companies to enrich", min_value=1, max_value=500, value=25, step=10)
    with col2:
        only_missing = st.checkbox("Only rows missing enrichment", value=True)
    with col3:
        include_financials = st.checkbox("Fetch financial reports", value=True)

    include_ownership = st.checkbox("Fetch shareholders / owners", value=True)
    include_ubos = st.checkbox("Fetch UBO / end-owner chain", value=True)

    if st.button("Run Enrichment", type="primary"):
        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return

        with st.spinner("Running OpenRegister enrichment..."):
            result = run_enrichment(
                openregister_api_key=openregister_api_key,
                supabase=supabase,
                limit=int(limit),
                only_missing=only_missing,
                include_financials=include_financials,
                include_ownership=include_ownership,
                include_ubos=include_ubos,
            )

        st.success(
            f"Enrichment completed. Processed {result['processed']} companies, "
            f"errors {len(result.get('errors', []))}."
        )

        if result.get("errors"):
            st.dataframe(pd.DataFrame(result["errors"]), use_container_width=True)


def claude_tab(supabase, claude_api_key: str, default_model: str):
    st.header("Claude Business Model")

    col1, col2 = st.columns(2)
    with col1:
        limit = st.number_input("Companies to process", min_value=1, max_value=500, value=25, step=10)
    with col2:
        model = st.text_input("Claude model", value=default_model)

    only_missing = st.checkbox("Only companies missing business model", value=True)

    if st.button("Run Claude Business Model Enrichment", type="primary"):
        if not claude_api_key:
            st.error("Paste your Claude API key in the sidebar first.")
            return

        with st.spinner("Running Claude business model enrichment..."):
            result = run_claude_business_model_enrichment(
                supabase=supabase,
                claude_api_key=claude_api_key,
                model=model,
                limit=int(limit),
                only_missing=only_missing,
            )

        st.success(
            f"Claude business model enrichment finished. "
            f"Processed {result['processed']} companies, errors {len(result.get('errors', []))}."
        )

        if result.get("rows"):
            st.dataframe(pd.DataFrame(result["rows"]), use_container_width=True)

        if result.get("errors"):
            st.warning("Errors")
            st.dataframe(pd.DataFrame(result["errors"]), use_container_width=True)


def fit_scoring_tab(supabase, claude_api_key: str, default_model: str):
    st.header("Claude Fit Scoring")

    with st.form("fit_scoring_config"):
        col1, col2 = st.columns(2)
        with col1:
            limit = st.number_input("Companies to score", min_value=1, max_value=500, value=25, step=10)
            model = st.text_input("Claude model", value=default_model)
            only_missing = st.checkbox("Only companies missing fit score", value=True)
        with col2:
            score_goal = st.text_area(
                "Target acquisition profile",
                value=DEFAULT_FIT_CONFIG["score_goal"],
                height=160,
            )

        st.subheader("Dynamic scoring weights")
        weights = {}
        for key, value in DEFAULT_FIT_CONFIG["weights"].items():
            weights[key] = st.slider(key, min_value=0, max_value=10, value=int(value), step=1)

        st.subheader("Hard filters / preference notes")
        hard_filters = st.text_area(
            "Hard filters",
            value=DEFAULT_FIT_CONFIG["hard_filters"],
            height=120,
        )

        submitted = st.form_submit_button("Run Fit Scoring", type="primary")

    if submitted:
        if not claude_api_key:
            st.error("Paste your Claude API key in the sidebar first.")
            return

        config = {
            "score_goal": score_goal,
            "weights": weights,
            "hard_filters": hard_filters,
        }

        with st.spinner("Running Claude fit scoring..."):
            result = run_fit_scoring(
                supabase=supabase,
                claude_api_key=claude_api_key,
                model=model,
                limit=int(limit),
                only_missing=only_missing,
                config=config,
            )

        st.success(
            f"Fit scoring finished. Processed {result['processed']} companies, "
            f"errors {len(result.get('errors', []))}."
        )

        if result.get("rows"):
            st.dataframe(pd.DataFrame(result["rows"]), use_container_width=True)

        if result.get("errors"):
            st.warning("Errors")
            st.dataframe(pd.DataFrame(result["errors"]), use_container_width=True)


def sheets_tab(supabase):
    st.header("Google Sheets Sync")

    st.info("Google Sheets credentials and spreadsheet ID are read from Streamlit secrets.")

    if st.button("Sync Supabase to Google Sheets", type="primary"):
        with st.spinner("Syncing Supabase tables to Google Sheets..."):
            result = sync_supabase_to_google_sheets(supabase)

        st.success("Google Sheets sync completed.")
        st.json(result)


def _filter_dataframe_for_export(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    filtered = df.copy()

    company_contains = filters.get("company_contains")
    if company_contains and "company_name" in filtered.columns:
        filtered = filtered[
            filtered["company_name"].fillna("").str.contains(company_contains, case=False, na=False)
        ]

    industry_contains = filters.get("industry_contains")
    if industry_contains and "industry_codes" in filtered.columns:
        filtered = filtered[
            filtered["industry_codes"].astype(str).str.contains(industry_contains, case=False, na=False)
        ]

    legal_forms = filters.get("legal_forms") or []
    if legal_forms and "legal_form" in filtered.columns:
        filtered = filtered[filtered["legal_form"].isin(legal_forms)]

    min_fit_score = filters.get("min_fit_score")
    if min_fit_score is not None and "fit_score" in filtered.columns:
        filtered = filtered[pd.to_numeric(filtered["fit_score"], errors="coerce") >= min_fit_score]

    for field, range_values in filters.get("numeric_ranges", {}).items():
        filtered = apply_numeric_filter(
            filtered,
            field,
            range_values.get("min"),
            range_values.get("max"),
        )

    return filtered


def filtered_export_tab(supabase):
    st.header("Filtered Workbook Export")

    overview_rows = fetch_all_rows_paginated(supabase, "master_overview")
    overview_df = pd.DataFrame(overview_rows)

    if overview_df.empty:
        st.info("No overview data available yet.")
        return

    with st.form("filtered_export_form"):
        col1, col2, col3 = st.columns(3)

        with col1:
            company_contains = st.text_input("Company name contains")
            industry_contains = st.text_input("Industry code contains", placeholder="Example: 10.51")

        with col2:
            legal_forms = st.multiselect(
                "Legal forms",
                options=list(LEGAL_FORM_OPTIONS.values()),
                default=[],
            )
            min_fit_score = st.number_input(
                "Minimum fit score",
                min_value=0,
                max_value=5,
                value=None,
                step=1,
                placeholder="Leave blank",
            )

        with col3:
            st.caption("Numeric filters")

        numeric_ranges = {}
        numeric_filter_fields = [
            ("Revenue EUR", "revenue_eur"),
            ("Employees", "employees"),
            ("Net income EUR", "net_income_eur"),
            ("Equity EUR", "equity_eur"),
            ("Direct owner age", "youngest_owner_age"),
            ("Main UBO age", "main_ubo_age"),
            ("Main UBO %", "main_ubo_percentage_share"),
            ("Main UBO max %", "main_ubo_max_percentage_share"),
        ]

        for label, field in numeric_filter_fields:
            with st.expander(label, expanded=False):
                min_value = optional_float_input(f"Min {label}", key=f"export_{field}_min")
                max_value = optional_float_input(f"Max {label}", key=f"export_{field}_max")
                if min_value is not None or max_value is not None:
                    numeric_ranges[field] = {"min": min_value, "max": max_value}

        submitted = st.form_submit_button("Build filtered workbook", type="primary")

    if submitted:
        filters = {
            "company_contains": company_contains,
            "industry_contains": industry_contains,
            "legal_forms": legal_forms,
            "min_fit_score": min_fit_score,
            "numeric_ranges": numeric_ranges,
        }

        filtered_df = _filter_dataframe_for_export(overview_df, filters)
        st.write(f"Filtered companies: {len(filtered_df)}")

        if filtered_df.empty:
            st.warning("No companies match the selected filters.")
            return

        workbook_bytes = build_filtered_workbook_bytes(supabase, filtered_df)

        st.download_button(
            label="Download filtered workbook",
            data=workbook_bytes,
            file_name="filtered_company_workbook.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.dataframe(filtered_df, use_container_width=True)


def main():
    st.title("CoK Nachfolge Pipeline")

    try:
        supabase = get_supabase_client()
    except Exception as exc:
        st.error(f"Could not connect to Supabase: {exc}")
        return

    with st.sidebar:
        st.header("API Keys")
        openregister_api_key = st.text_input(
            "OpenRegister API key",
            type="password",
            value=st.session_state.get("openregister_api_key", ""),
        )
        st.session_state["openregister_api_key"] = openregister_api_key

        claude_api_key = st.text_input(
            "Claude API key",
            type="password",
            value=st.session_state.get("claude_api_key", ""),
        )
        st.session_state["claude_api_key"] = claude_api_key

        default_claude_model = st.text_input(
            "Default Claude model",
            value="claude-3-5-sonnet-latest",
        )

    tab_search, tab_northdata, tab_enrich, tab_claude, tab_fit, tab_sheets, tab_export = st.tabs([
        "Filter Search",
        "NorthData Import",
        "OpenRegister Enrichment",
        "Claude Business Model",
        "Claude Fit Scoring",
        "Google Sheets Sync",
        "Filtered Workbook Export",
    ])

    with tab_search:
        search_tab(supabase, openregister_api_key)
    with tab_northdata:
        northdata_import_tab(supabase, openregister_api_key)
    with tab_enrich:
        enrichment_tab(supabase, openregister_api_key)
    with tab_claude:
        claude_tab(supabase, claude_api_key, default_claude_model)
    with tab_fit:
        fit_scoring_tab(supabase, claude_api_key, default_claude_model)
    with tab_sheets:
        sheets_tab(supabase)
    with tab_export:
        filtered_export_tab(supabase)


if __name__ == "__main__":
    main()
