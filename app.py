from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.claude_business_model import run_claude_business_model_enrichment
from modules.filtered_workbook_export import apply_numeric_filter, build_filtered_workbook_bytes, fetch_all_rows_paginated
from modules.fit_scoring import DEFAULT_FIT_CONFIG, run_fit_scoring
from modules.google_sheets_sync import sync_supabase_to_google_sheets
from modules.openregister_enrichment import run_enrichment
from modules.openregister_search import run_company_search, validate_filter_config
from modules.supabase_client import get_supabase_client
from modules.utils import parse_csv_values

st.set_page_config(page_title="Succession Analysis OpenRegister", page_icon="📊", layout="wide")

MAIN_LEGAL_FORM_OPTIONS = {
    "GmbH": "gmbh",
    "UG": "ug",
    "GmbH & Co. KG / KG": "kg",
    "OHG": "ohg",
    "e.K.": "ek",
    "gGmbH": "ggmbh",
    "GbR / eGbR": "gbr",
}

OTHER_LEGAL_FORM_OPTIONS = {
    "AG": "ag",
    "SE": "se",
    "KGaA": "kgaa",
    "eG": "eg",
    "e.V.": "ev",
    "EWIV": "ewiv",
    "Foreign legal form": "foreign",
    "LLP": "llp",
    "Municipal": "municipal",
    "Unknown": "unknown",
}

LEGAL_FORM_OPTIONS = {
    **MAIN_LEGAL_FORM_OPTIONS,
    **OTHER_LEGAL_FORM_OPTIONS,
}

DEFAULT_LEGAL_FORMS = {
    "GmbH",
    "UG",
    "GmbH & Co. KG / KG",
    "OHG",
    "e.K.",
}

FINANCIAL_FIELDS = [
    ("revenue", "Revenue (€)"),
    ("employees", "Employees"),
    ("balance_sheet_total", "Balance sheet total (€)"),
    ("net_income", "Net income (€)"),
    ("equity", "Equity (€)"),
    ("cash", "Cash (€)"),
    ("liabilities", "Liabilities (€)"),
    ("real_estate", "Real estate (€)"),
    ("capital_amount", "Capital amount (€)"),
]


def bool_filter(label: str, key: str, *, disabled: bool = False, index: int = 0):
    value = st.selectbox(label, ["Any", "Yes", "No"], key=key, disabled=disabled, index=index)
    if value == "Yes":
        return True
    if value == "No":
        return False
    return None


def optional_int_input(label: str, key: str, *, min_value: int = 0, step: int = 1, placeholder: str = "Leave blank"):
    return st.number_input(label, min_value=min_value, value=None, step=step, placeholder=placeholder, key=key)


def optional_float_input(label: str, key: str, *, min_value: float = 0.0, step: float = 1.0, placeholder: str = "Leave blank"):
    return st.number_input(label, min_value=min_value, value=None, step=step, placeholder=placeholder, key=key)


def financial_range_inputs() -> dict[str, float | None]:
    st.subheader("Financial / company-size filters")
    st.caption("Leave blank to ignore that side of the range. The app sends money filters to OpenRegister in cents automatically.")
    config: dict[str, float | None] = {}
    for field, label in FINANCIAL_FIELDS:
        c1, c2 = st.columns(2)
        if field == "employees":
            with c1:
                min_val = optional_int_input(f"{label} min", key=f"{field}_min")
            with c2:
                max_val = optional_int_input(f"{label} max", key=f"{field}_max")
        else:
            with c1:
                min_val = optional_float_input(f"{label} min", key=f"{field}_min", step=1000.0)
            with c2:
                max_val = optional_float_input(f"{label} max", key=f"{field}_max", step=1000.0)
        config[f"{field}_min"] = min_val
        config[f"{field}_max"] = max_val
    return config


def search_tab(supabase, openregister_api_key: str):
    st.header("Filter Search")
    st.caption("Search companies directly in OpenRegister and save matched companies to Supabase. Each company is deduped by OpenRegister company ID.")

    with st.form("openregister_filter_search"):
        search_name = st.text_input("Search name", value="Succession target search")
        max_companies = st.number_input("Max companies to fetch from search", min_value=1, max_value=5000, value=100, step=25)

        st.subheader("Company filters")
        active_only = st.checkbox("Active companies only", value=True)

        st.write("Legal forms")
        legal_forms = []

        st.caption("Main succession forms are shown first. Advanced forms are available below but are not selected by default.")

        with st.container():
            st.markdown("**Main succession legal forms**")
            cols = st.columns(4)
            for i, (label, value) in enumerate(MAIN_LEGAL_FORM_OPTIONS.items()):
                with cols[i % 4]:
                    if st.checkbox(
                        label,
                        value=label in DEFAULT_LEGAL_FORMS,
                        key=f"legal_form_{value}",
                    ):
                        legal_forms.append(value)

        with st.expander("Advanced / other legal forms", expanded=False):
            cols = st.columns(4)
            for i, (label, value) in enumerate(OTHER_LEGAL_FORM_OPTIONS.items()):
                with cols[i % 4]:
                    if st.checkbox(
                        label,
                        value=False,
                        key=f"legal_form_{value}",
                    ):
                        legal_forms.append(value)

        industry_codes_text = st.text_input("Industry codes", placeholder="Exact WZ2025 codes, e.g. 10.11, 10.51, 20.42")
        industry_code_match_mode = st.radio(
            "Industry code match",
            ["Any selected code (OR)", "All selected codes (AND)"],
            horizontal=True,
            help="OR returns companies with at least one listed WZ code. AND returns only companies containing every listed WZ code.",
        )
        purpose_text = st.text_input("Business purpose keywords", placeholder="Optional. Example: Maschinenbau, Software, Pflege")

        financial_config = financial_range_inputs()

        st.subheader("Ownership / succession filters")
        c1, c2, c3 = st.columns(3)
        with c1:
            has_sole_owner = bool_filter("Has sole owner", "has_sole_owner_filter")
        with c2:
            has_representative_owner = bool_filter("Owner-managed", "has_representative_owner_filter")
        with c3:
            is_family_owned = bool_filter("Family-owned", "is_family_owned_filter")

        owner_cols = st.columns(2)
        if has_sole_owner is True:
            with owner_cols[0]:
                st.number_input("Number of owners min", value=1, disabled=True)
                number_of_owners_min = 1
            with owner_cols[1]:
                st.number_input("Number of owners max", value=1, disabled=True)
                number_of_owners_max = 1
            st.caption("Sole-owner = Yes forces number of owners to exactly 1.")
        else:
            with owner_cols[0]:
                number_of_owners_min = optional_int_input("Number of owners min", key="number_of_owners_min")
            with owner_cols[1]:
                number_of_owners_max = optional_int_input("Number of owners max", key="number_of_owners_max")

        age_cols = st.columns(2)
        with age_cols[0]:
            youngest_owner_age_min = optional_int_input("Youngest owner age min", key="youngest_owner_age_min")
        with age_cols[1]:
            youngest_owner_age_max = optional_int_input("Youngest owner age max", key="youngest_owner_age_max")

        submitted = st.form_submit_button("Run search and save companies", type="primary")

    if submitted:
        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return
        config = {
            "active_only": active_only,
            "legal_forms": legal_forms,
            "industry_codes": parse_csv_values(industry_codes_text),
            "industry_code_match_mode": "all" if industry_code_match_mode.startswith("All") else "any",
            "purpose_keywords": parse_csv_values(purpose_text),
            **financial_config,
            "number_of_owners_min": number_of_owners_min,
            "number_of_owners_max": number_of_owners_max,
            "youngest_owner_age_min": youngest_owner_age_min,
            "youngest_owner_age_max": youngest_owner_age_max,
            "has_sole_owner": has_sole_owner,
            "has_representative_owner": has_representative_owner,
            "is_family_owned": is_family_owned,
        }
        errors = validate_filter_config(config)
        if errors:
            st.error("Fix these filter conflicts before running the search:")
            for err in errors:
                st.write(f"- {err}")
            return

        with st.spinner("Running OpenRegister search and saving companies..."):
            result = run_company_search(
                api_key=openregister_api_key,
                supabase=supabase,
                search_name=search_name,
                filter_config=config,
                max_companies=int(max_companies),
            )
        if result["ok"]:
            st.success(f"Search complete. Returned {result['returned']} companies and saved/upserted {result['saved']} rows.")
            with st.expander("OpenRegister filters sent"):
                st.json(result["filters"])
            if result["rows"]:
                st.dataframe(pd.DataFrame(result["rows"]), use_container_width=True)
        else:
            st.error(result.get("error", "Search failed."))
            with st.expander("OpenRegister filters sent"):
                st.json(result.get("filters", []))


def enrichment_tab(supabase, openregister_api_key: str):
    st.header("OpenRegister Enrichment")
    st.caption("Run selected OpenRegister enrichment endpoints for companies saved in Supabase.")

    c1, c2 = st.columns(2)
    with c1:
        existing_behavior = st.radio("Existing enrichment behavior", ["Skip existing", "Update existing"], horizontal=True)
        st.caption("Skip existing avoids repeat API calls for sections that already have timestamps. Update existing re-fetches selected sections.")
    with c2:
        fetch_company_info = st.checkbox("Company info", value=True)
        fetch_financials = st.checkbox("Financials", value=True)
        fetch_ownership = st.checkbox("Ownership", value=True)
        fetch_ubos = st.checkbox("UBOs", value=False)

    if st.button("Run OpenRegister enrichment", type="primary"):
        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return
        if not any([fetch_company_info, fetch_financials, fetch_ownership, fetch_ubos]):
            st.error("Select at least one enrichment type.")
            return
        with st.spinner("Running enrichment. This may use OpenRegister credits..."):
            result = run_enrichment(
                api_key=openregister_api_key,
                supabase=supabase,
                update_existing=existing_behavior == "Update existing",
                fetch_company_info=fetch_company_info,
                fetch_financials=fetch_financials,
                fetch_ownership=fetch_ownership,
                fetch_ubos=fetch_ubos,
            )
        st.success(f"Enrichment finished for {result['companies_seen']} backend companies.")
        if result["results"]:
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)


def claude_tab(supabase, claude_api_key: str, default_model_name: str):
    st.header("Claude Business Model")
    st.caption("Scrapes company websites, asks Claude for a concise business model summary and normalized segment, then saves to company_models.")

    c1, c2 = st.columns(2)
    with c1:
        update_existing = st.radio("Existing Claude summaries", ["Skip existing", "Update existing"], horizontal=True)
    with c2:
        model_name = st.text_input("Claude model for business summaries", value=default_model_name)

    if st.button("Run Claude business model enrichment", type="primary"):
        if not claude_api_key:
            st.error("Paste your Claude / Anthropic API key in the sidebar first.")
            return
        with st.spinner("Running Claude business model enrichment..."):
            result = run_claude_business_model_enrichment(
                supabase=supabase,
                claude_api_key=claude_api_key,
                model_name=model_name,
                update_existing=update_existing == "Update existing",
            )
        st.success(
            f"Claude business model enrichment finished. Processed {result['processed']}, saved {result['saved']}, skipped {result['skipped']}, errors {result['errors']}."
        )
        if result["results"]:
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)


def fit_scoring_tab(supabase, claude_api_key: str, default_model_name: str):
    st.header("Claude Fit Scoring")
    st.caption("Scores companies using OpenRegister financials, direct owners, UBO/control-chain data, and Claude business model summaries.")

    with st.form("fit_scoring_form"):
        c0, c1 = st.columns(2)
        with c0:
            update_existing = st.radio("Existing fit scores", ["Skip existing", "Update existing"], horizontal=True)
        with c1:
            model_name = st.text_input("Claude model for fit scoring", value=default_model_name)

        st.subheader("Dynamic scoring parameters")
        c1, c2, c3 = st.columns(3)
        with c1:
            revenue_min = st.number_input("Revenue min EUR", min_value=0.0, value=float(DEFAULT_FIT_CONFIG["revenue_min"]), step=100000.0)
            revenue_max = st.number_input("Revenue max EUR", min_value=0.0, value=float(DEFAULT_FIT_CONFIG["revenue_max"]), step=100000.0)
            employees_min = st.number_input("Minimum employees", min_value=0, value=int(DEFAULT_FIT_CONFIG["employees_min"]), step=1)
        with c2:
            employees_max = st.number_input("Maximum employees", min_value=0, value=int(DEFAULT_FIT_CONFIG["employees_max"]), step=1)
            equity_ratio_min = st.number_input("Minimum equity ratio %", min_value=0.0, value=float(DEFAULT_FIT_CONFIG["equity_ratio_min"]), step=1.0)
            equity_ratio_good = st.number_input("Good equity ratio %", min_value=0.0, value=float(DEFAULT_FIT_CONFIG["equity_ratio_good"]), step=1.0)
        with c3:
            min_shareholder_age = st.number_input("Minimum shareholder age", min_value=0, value=int(DEFAULT_FIT_CONFIG["min_shareholder_age"]), step=1)
            preferred_business_type = st.text_input("Preferred business type", value=str(DEFAULT_FIT_CONFIG["preferred_business_type"]))

        preferred_industries = st.text_input("Preferred industries", value=str(DEFAULT_FIT_CONFIG["preferred_industries"]))
        profit_proxy_target = st.text_input("Profit / EBITDA target logic", value=str(DEFAULT_FIT_CONFIG["profit_proxy_target"]))
        additional_instructions = st.text_area("Additional scoring instructions", value=str(DEFAULT_FIT_CONFIG["additional_instructions"]), height=120)

        submitted = st.form_submit_button("Run Claude fit scoring", type="primary")

    if submitted:
        if not claude_api_key:
            st.error("Paste your Claude / Anthropic API key in the sidebar first.")
            return
        if revenue_min > revenue_max and revenue_max > 0:
            st.error("Revenue minimum cannot be greater than maximum.")
            return
        if employees_min > employees_max and employees_max > 0:
            st.error("Minimum employees cannot be greater than maximum employees.")
            return
        fit_config = {
            "revenue_min": revenue_min,
            "revenue_max": revenue_max,
            "employees_min": employees_min,
            "employees_max": employees_max,
            "equity_ratio_min": equity_ratio_min,
            "equity_ratio_good": equity_ratio_good,
            "min_shareholder_age": min_shareholder_age,
            "preferred_business_type": preferred_business_type,
            "preferred_industries": preferred_industries,
            "profit_proxy_target": profit_proxy_target,
            "additional_instructions": additional_instructions,
        }
        with st.spinner("Running Claude fit scoring..."):
            result = run_fit_scoring(
                supabase=supabase,
                claude_api_key=claude_api_key,
                model_name=model_name,
                fit_config=fit_config,
                update_existing=update_existing == "Update existing",
            )
        st.success(f"Fit scoring finished. Scored {result['scored']}, skipped {result['skipped']}, errors {result['errors']}.")
        if result["results"]:
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)


def sheets_tab(supabase):
    st.header("Google Sheets Sync")
    st.caption("Writes Supabase data to the configured Google Sheet. Supabase remains the source of truth.")
    if st.button("Sync Supabase to Google Sheets", type="primary"):
        with st.spinner("Syncing to Google Sheets..."):
            try:
                counts = sync_supabase_to_google_sheets(supabase)
                st.success("Google Sheets sync complete.")
                st.dataframe(pd.DataFrame([{"Sheet": k, "Rows": v} for k, v in counts.items()]), use_container_width=True)
            except Exception as exc:
                st.error(f"Google Sheets sync failed: {exc}")


def _filter_dataframe_for_export(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    if filters.get("company_contains"):
        text = filters["company_contains"].strip()
        if text and "company_name" in df.columns:
            df = df[df["company_name"].fillna("").astype(str).str.contains(text, case=False, na=False)]
    if filters.get("legal_forms") and "legal_form" in df.columns:
        df = df[df["legal_form"].isin(filters["legal_forms"])]
    if filters.get("industry_contains") and "industry_codes" in df.columns:
        text = filters["industry_contains"].strip()
        if text:
            df = df[df["industry_codes"].fillna("").astype(str).str.contains(text, case=False, na=False)]
    for col, op, v1, v2 in filters.get("numeric", []):
        df = apply_numeric_filter(df, col, op, v1, v2)
    return df


def filtered_export_tab(supabase):
    st.header("Filtered Workbook Export")
    st.caption("Generate a downloadable Excel workbook from filtered backend data. The workbook includes Overview plus related Companies, Financials, Owners, UBOs, Claude Models, Fit Scores, and Logs.")

    with st.form("filtered_export_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            company_contains = st.text_input("Company name contains")
            industry_contains = st.text_input("Industry code contains", placeholder="Example: 10.51")
        with c2:
            legal_form_labels = st.multiselect(
                "Legal forms",
                options=list(LEGAL_FORM_OPTIONS.keys()),
                default=[],
            )
            legal_forms = [LEGAL_FORM_OPTIONS[label] for label in legal_form_labels]
        with c3:
            min_fit_score = st.number_input("Minimum fit score", min_value=0, max_value=5, value=None, step=1, placeholder="Leave blank")

        st.subheader("Numeric filters")
        numeric_specs = []
        invalid_numeric_filters = []
        fields = [
            ("Revenue EUR", "revenue_eur", 100000.0),
            ("Employees", "employees", 1.0),
            ("Net income EUR", "net_income_eur", 100000.0),
            ("Equity EUR", "equity_eur", 100000.0),
            ("Direct owner age", "youngest_owner_age", 1.0),
            ("Main UBO age", "main_ubo_age", 1.0),
            ("Main UBO %", "main_ubo_percentage_share", 1.0),
            ("Main UBO max %", "main_ubo_max_percentage_share", 1.0),
        ]
        for label, key, step in fields:
            c1, c2, c3 = st.columns(3)
            with c1:
                op = st.selectbox(f"{label} operator", ["Ignore", "=", ">", ">=", "<", "<=", "Between"], key=f"export_{key}_op")
            with c2:
                v1 = st.number_input(f"{label} value", min_value=0.0, value=None, step=step, placeholder="Leave blank", key=f"export_{key}_v1")
            with c3:
                v2 = st.number_input(f"{label} upper", min_value=0.0, value=None, step=step, placeholder="Leave blank", key=f"export_{key}_v2")
            if op != "Ignore":
                if v1 is None or (op == "Between" and v2 is None):
                    invalid_numeric_filters.append(label)
                else:
                    numeric_specs.append((key, op, v1, v2))

        submitted = st.form_submit_button("Generate filtered workbook", type="primary")

    if submitted:
        if invalid_numeric_filters:
            st.error("Add values for these selected numeric filters: " + ", ".join(invalid_numeric_filters))
            return
        try:
            rows = fetch_all_rows_paginated(supabase, "master_overview")
            df = pd.DataFrame(rows)
            if df.empty:
                st.warning("No data found in master_overview.")
                return
            filters = {
                "company_contains": company_contains,
                "industry_contains": industry_contains,
                "legal_forms": legal_forms,
                "numeric": numeric_specs,
            }
            if min_fit_score is not None and min_fit_score > 0:
                filters["numeric"].append(("fit_score", ">=", float(min_fit_score), None))
            filtered = _filter_dataframe_for_export(df, filters)
            if filtered.empty:
                st.warning("No companies matched the selected filters.")
                return
            sort_cols = [c for c in ["company_name", "register_id"] if c in filtered.columns]
            if sort_cols:
                filtered = filtered.sort_values(by=sort_cols)
            register_ids = list(dict.fromkeys(filtered["register_id"].dropna().astype(str).tolist()))
            export_result = build_filtered_workbook_bytes(
                supabase,
                register_ids=register_ids,
                overview_rows=filtered.to_dict("records"),
            )
            st.success(f"Filtered workbook created for {len(register_ids)} companies.")
            st.write("Rows per sheet:")
            st.json(export_result["table_counts"])
            st.download_button(
                "Download filtered workbook",
                data=export_result["workbook_bytes"],
                file_name="filtered_openregister_workbook.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except Exception as exc:
            st.error("Filtered export failed.")
            st.exception(exc)


def main():
    st.title("Succession Analysis — OpenRegister")
    st.caption("OpenRegister search → Supabase backend → enrichment → Claude scoring → Google Sheets / Excel export")

    with st.sidebar:
        st.header("Configuration")
        openregister_api_key = st.text_input("OpenRegister API key", type="password")
        claude_api_key = st.text_input("Claude / Anthropic API key", type="password")
        default_claude_model = st.text_input("Default Claude model", value="claude-sonnet-4-5")
        st.info("Supabase and Google Sheets credentials come from Streamlit secrets. OpenRegister and Claude keys are pasted here.")

    try:
        supabase = get_supabase_client()
    except Exception as exc:
        st.error(f"Supabase connection failed: {exc}")
        st.stop()

    tab_search, tab_enrich, tab_claude, tab_fit, tab_sheets, tab_export = st.tabs([
        "Filter Search",
        "OpenRegister Enrichment",
        "Claude Business Model",
        "Claude Fit Scoring",
        "Google Sheets Sync",
        "Filtered Workbook Export",
    ])

    with tab_search:
        search_tab(supabase, openregister_api_key)
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
