from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.claude_business_model import run_claude_business_model_enrichment
from modules.filtered_workbook_export import (
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

st.set_page_config(page_title="Succession Analysis OpenRegister", page_icon="📊", layout="wide")

LEGAL_FORM_OPTIONS = {
    "GmbH": "gmbh",
    "UG": "ug",
    "GmbH & Co. KG / KG": "kg",
    "OHG": "ohg",
    "e.K.": "ek",
}
DEFAULT_LEGAL_FORMS = {"GmbH", "UG", "GmbH & Co. KG / KG", "OHG", "e.K."}

FINANCIAL_FIELDS = [
    ("revenue", "OpenRegister Revenue (€)"),
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


def optional_int_input(
    label: str,
    key: str,
    *,
    min_value: int = 0,
    step: int = 1,
    placeholder: str = "Leave blank",
):
    return st.number_input(
        label,
        min_value=min_value,
        value=None,
        step=step,
        placeholder=placeholder,
        key=key,
    )


def optional_float_input(
    label: str,
    key: str,
    *,
    min_value: float = 0.0,
    step: float = 1.0,
    placeholder: str = "Leave blank",
):
    return st.number_input(
        label,
        min_value=min_value,
        value=None,
        step=step,
        placeholder=placeholder,
        key=key,
    )


def financial_range_inputs() -> dict[str, float | None]:
    st.subheader("Financial / company-size filters")
    st.caption(
        "Leave blank to ignore that side of the range. "
        "The app sends money filters to OpenRegister in cents automatically."
    )

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
                min_val = optional_float_input(
                    f"{label} min",
                    key=f"{field}_min",
                    step=1000.0,
                )
            with c2:
                max_val = optional_float_input(
                    f"{label} max",
                    key=f"{field}_max",
                    step=1000.0,
                )

        config[f"{field}_min"] = min_val
        config[f"{field}_max"] = max_val

    return config


def search_tab(supabase, openregister_api_key: str):
    st.header("Filter Search")
    st.caption(
        "Search companies directly in OpenRegister and save matched companies to Supabase. "
        "Each company is deduped by OpenRegister company ID."
    )

    with st.form("openregister_filter_search"):
        search_name = st.text_input("Search name", value="Succession target search")
        max_companies = st.number_input(
            "Max companies to fetch from search",
            min_value=1,
            max_value=5000,
            value=100,
            step=25,
        )

        st.subheader("Company filters")
        active_only = st.checkbox("Active companies only", value=True)

        st.write("Legal forms")
        legal_forms = []
        cols = st.columns(len(LEGAL_FORM_OPTIONS))

        for i, (label, value) in enumerate(LEGAL_FORM_OPTIONS.items()):
            with cols[i]:
                if st.checkbox(
                    label,
                    value=label in DEFAULT_LEGAL_FORMS,
                    key=f"legal_form_{value}",
                ):
                    legal_forms.append(value)

        industry_codes_text = st.text_input(
            "Industry codes",
            placeholder="Exact WZ2025 codes, e.g. 10.11, 10.51, 20.42",
        )

        industry_code_match_mode = st.radio(
            "Industry code match",
            ["Any selected code (OR)", "All selected codes (AND)"],
            horizontal=True,
            help=(
                "OR returns companies with at least one listed WZ code. "
                "AND returns only companies containing every listed WZ code."
            ),
        )

        purpose_text = st.text_input(
            "Business purpose keywords",
            placeholder="Optional. Example: Maschinenbau, Software, Pflege",
        )

        financial_config = financial_range_inputs()

        st.subheader("Shareholder / succession filters")

        c1, c2, c3 = st.columns(3)

        with c1:
            has_sole_owner = bool_filter("Has sole shareholder", "has_sole_owner_filter")
        with c2:
            has_representative_owner = bool_filter("Shareholder-managed", "has_representative_owner_filter")
        with c3:
            is_family_owned = bool_filter("Family-owned", "is_family_owned_filter")

        owner_cols = st.columns(2)

        if has_sole_owner is True:
            with owner_cols[0]:
                st.number_input("Number of shareholders min", value=1, disabled=True)
                number_of_owners_min = 1
            with owner_cols[1]:
                st.number_input("Number of shareholders max", value=1, disabled=True)
                number_of_owners_max = 1

            st.caption("Sole shareholder = Yes forces the OpenRegister owner/shareholder count to exactly 1.")
        else:
            with owner_cols[0]:
                number_of_owners_min = optional_int_input(
                    "Number of shareholders min",
                    key="number_of_owners_min",
                )
            with owner_cols[1]:
                number_of_owners_max = optional_int_input(
                    "Number of shareholders max",
                    key="number_of_owners_max",
                )

        age_cols = st.columns(2)

        with age_cols[0]:
            youngest_owner_age_min = optional_int_input(
                "Youngest shareholder age min",
                key="youngest_owner_age_min",
            )
        with age_cols[1]:
            youngest_owner_age_max = optional_int_input(
                "Youngest shareholder age max",
                key="youngest_owner_age_max",
            )

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
            st.success(
                f"Search complete. Returned {result['returned']} companies "
                f"and saved/upserted {result['saved']} rows."
            )

            with st.expander("OpenRegister filters sent"):
                st.json(result["filters"])

            if result["rows"]:
                st.dataframe(pd.DataFrame(result["rows"]), use_container_width=True)
        else:
            st.error(result.get("error", "Search failed."))

            with st.expander("OpenRegister filters sent"):
                st.json(result.get("filters", []))


def northdata_import_tab(supabase, openregister_api_key: str):
    st.header("NorthData Import")
    st.caption(
        "Upload a NorthData Excel file. Each row is matched to OpenRegister first. "
        "Only matched companies are inserted or updated using the real OpenRegister company ID."
    )

    uploaded_file = st.file_uploader(
        "Upload NorthData Excel file",
        type=["xlsx"],
        help="Only .xlsx files are supported.",
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
            f"skipped {result['skipped']}, errors {result['errors']}, "
            f"parse-warning rows {result.get('rows_with_parse_warnings', 0)}."
        )

        st.dataframe(
            pd.DataFrame([
                {
                    "Total rows": result["total_rows"],
                    "Imported new": result["imported"],
                    "Updated existing": result["updated"],
                    "Skipped": result["skipped"],
                    "Errors": result["errors"],
                    "Rows with parse warnings": result.get("rows_with_parse_warnings", 0),
                }
            ]),
            use_container_width=True,
        )

        if result.get("results"):
            st.subheader("Row results")
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)


def enrichment_tab(supabase, openregister_api_key: str):
    st.header("OpenRegister Enrichment")
    st.caption("Run selected OpenRegister enrichment endpoints for companies saved in Supabase.")

    c1, c2 = st.columns(2)

    with c1:
        existing_behavior = st.radio(
            "Existing enrichment behavior",
            ["Skip existing", "Update existing"],
            horizontal=True,
        )
        st.caption(
            "Skip existing avoids repeat API calls for sections that already have timestamps. "
            "Update existing re-fetches selected sections."
        )

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
    st.caption(
        "Scrapes company websites and asks Claude to save separate "
        "business_segment, business_model, and detailed summary fields."
    )

    c1, c2 = st.columns(2)

    with c1:
        update_existing = st.radio(
            "Existing Claude summaries",
            ["Skip existing", "Update existing"],
            horizontal=True,
        )

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
            f"Claude business model enrichment finished. "
            f"Processed {result['processed']}, saved {result['saved']}, "
            f"skipped {result['skipped']}, errors {result['errors']}."
        )

        if result["results"]:
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)


def fit_scoring_tab(supabase, claude_api_key: str, default_model_name: str):
    st.header("Claude Fit Scoring")
    st.caption(
        "Scores companies using source-separated Northdata/OpenRegister financial and WZ fields, "
        "company founding year, shareholders, UBO/control-chain data, and Claude business model summaries."
    )

    with st.form("fit_scoring_form"):
        c0, c1 = st.columns(2)

        with c0:
            update_existing = st.radio(
                "Existing fit scores",
                ["Skip existing", "Update existing"],
                horizontal=True,
            )

        with c1:
            model_name = st.text_input("Claude model for fit scoring", value=default_model_name)

        st.subheader("Dynamic scoring parameters")

        c1, c2, c3 = st.columns(3)

        with c1:
            revenue_min = st.number_input(
                "Revenue min EUR",
                min_value=0.0,
                value=float(DEFAULT_FIT_CONFIG["revenue_min"]),
                step=100000.0,
            )
            revenue_max = st.number_input(
                "Revenue max EUR",
                min_value=0.0,
                value=float(DEFAULT_FIT_CONFIG["revenue_max"]),
                step=100000.0,
            )
            employees_min = st.number_input(
                "Minimum employees",
                min_value=0,
                value=int(DEFAULT_FIT_CONFIG["employees_min"]),
                step=1,
            )

        with c2:
            employees_max = st.number_input(
                "Maximum employees",
                min_value=0,
                value=int(DEFAULT_FIT_CONFIG["employees_max"]),
                step=1,
            )
            equity_ratio_min = st.number_input(
                "Minimum equity ratio %",
                min_value=0.0,
                value=float(DEFAULT_FIT_CONFIG["equity_ratio_min"]),
                step=1.0,
            )
            equity_ratio_good = st.number_input(
                "Good equity ratio %",
                min_value=0.0,
                value=float(DEFAULT_FIT_CONFIG["equity_ratio_good"]),
                step=1.0,
            )

        with c3:
            min_shareholder_age = st.number_input(
                "Minimum shareholder age",
                min_value=0,
                value=int(DEFAULT_FIT_CONFIG["min_shareholder_age"]),
                step=1,
            )
            preferred_business_type = st.text_input(
                "Preferred business type",
                value=str(DEFAULT_FIT_CONFIG["preferred_business_type"]),
            )

        preferred_industries = st.text_input(
            "Preferred industries",
            value=str(DEFAULT_FIT_CONFIG["preferred_industries"]),
        )
        profit_proxy_target = st.text_input(
            "Profit / EBITDA target logic",
            value=str(DEFAULT_FIT_CONFIG["profit_proxy_target"]),
        )
        additional_instructions = st.text_area(
            "Additional scoring instructions",
            value=str(DEFAULT_FIT_CONFIG["additional_instructions"]),
            height=120,
        )

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

        st.success(
            f"Fit scoring finished. Scored {result['scored']}, "
            f"skipped {result['skipped']}, errors {result['errors']}."
        )

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
                st.dataframe(
                    pd.DataFrame([{"Sheet": k, "Rows": v} for k, v in counts.items()]),
                    use_container_width=True,
                )
            except Exception as exc:
                st.error(f"Google Sheets sync failed: {exc}")


def _filter_dataframe_for_export(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df

    def contains_any(series: pd.Series, values: list[str]) -> pd.Series:
        mask = pd.Series(False, index=series.index)

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            mask = mask | series.fillna("").astype(str).str.contains(
                text,
                case=False,
                na=False,
                regex=False,
            )

        return mask

    legal_form_terms = filters.get("legal_form_terms") or []

    if legal_form_terms and "legal_form" in df.columns:
        df = df[contains_any(df["legal_form"], legal_form_terms)]

    northdata_wz_terms = filters.get("northdata_wz_terms") or []

    if northdata_wz_terms and "northdata_wz_code" in df.columns:
        df = df[contains_any(df["northdata_wz_code"], northdata_wz_terms)]

    for item in filters.get("ranges", []):
        column = item["column"]
        min_value = item.get("min")
        max_value = item.get("max")

        if column not in df.columns:
            continue

        series = pd.to_numeric(df[column], errors="coerce")

        if min_value is not None:
            df = df[series >= min_value]
            series = pd.to_numeric(df[column], errors="coerce")

        if max_value is not None:
            df = df[series <= max_value]

    shareholder_age_min = filters.get("shareholder_age_min")
    shareholder_age_max = filters.get("shareholder_age_max")

    if shareholder_age_min is not None and "youngest_shareholder_age" in df.columns:
        youngest = pd.to_numeric(df["youngest_shareholder_age"], errors="coerce")
        df = df[youngest >= shareholder_age_min]

    if shareholder_age_max is not None and "oldest_shareholder_age" in df.columns:
        oldest = pd.to_numeric(df["oldest_shareholder_age"], errors="coerce")
        df = df[oldest <= shareholder_age_max]

    return df


def filtered_export_tab(supabase):
    st.header("Filtered Workbook Export")
    st.caption(
        "Generate a downloadable Excel workbook from filtered backend data. "
        "Filters use direct shareholder/company-level fields only; UBO fields are not used as filters."
    )

    def int_input(
        label: str,
        key: str,
        *,
        min_value: int | None = None,
        max_value: int | None = None,
    ):
        return st.number_input(
            label,
            min_value=min_value,
            max_value=max_value,
            value=None,
            step=1,
            placeholder="Leave blank",
            key=key,
        )

    def money_input(label: str, key: str, *, min_value: float | None = None):
        return st.number_input(
            label,
            min_value=min_value,
            value=None,
            step=100000.0,
            placeholder="Leave blank",
            key=key,
        )

    def validate_min_max(label: str, min_value, max_value, errors: list[str]) -> None:
        if min_value is not None and max_value is not None and max_value < min_value:
            errors.append(f"{label}: maximum cannot be less than minimum.")

    with st.form("filtered_export_form"):
        c1, c2 = st.columns(2)

        with c1:
            legal_forms_text = st.text_input(
                "Legal forms contains",
                placeholder="Example: gmbh or gmbh, kg, ag",
            )

        with c2:
            northdata_wz_text = st.text_input(
                "Northdata WZ code contains",
                placeholder="Example: 10.69 or 10.67, 11.51, 12",
            )

        st.subheader("Range filters")
        st.caption("Leave both fields blank to ignore a filter.")

        c1, c2 = st.columns(2)
        with c1:
            fit_score_min = int_input("Minimum fit score", "export_fit_score_min", min_value=0, max_value=5)
        with c2:
            fit_score_max = int_input("Maximum fit score", "export_fit_score_max", min_value=0, max_value=5)

        st.markdown("**Employees**")
        c1, c2 = st.columns(2)
        with c1:
            northdata_employees_min = int_input(
                "Minimum Northdata employees",
                "export_northdata_employees_min",
                min_value=0,
            )
        with c2:
            northdata_employees_max = int_input(
                "Maximum Northdata employees",
                "export_northdata_employees_max",
                min_value=0,
            )

        c1, c2 = st.columns(2)
        with c1:
            openregister_employees_min = int_input(
                "Minimum OpenRegister employees",
                "export_openregister_employees_min",
                min_value=0,
            )
        with c2:
            openregister_employees_max = int_input(
                "Maximum OpenRegister employees",
                "export_openregister_employees_max",
                min_value=0,
            )

        st.markdown("**Revenue**")
        c1, c2 = st.columns(2)
        with c1:
            northdata_revenue_min = money_input(
                "Minimum Northdata revenue EUR",
                "export_northdata_revenue_min",
                min_value=0.0,
            )
        with c2:
            northdata_revenue_max = money_input(
                "Maximum Northdata revenue EUR",
                "export_northdata_revenue_max",
                min_value=0.0,
            )

        c1, c2 = st.columns(2)
        with c1:
            openregister_revenue_min = money_input(
                "Minimum OpenRegister revenue EUR",
                "export_openregister_revenue_min",
                min_value=0.0,
            )
        with c2:
            openregister_revenue_max = money_input(
                "Maximum OpenRegister revenue EUR",
                "export_openregister_revenue_max",
                min_value=0.0,
            )

        st.markdown("**Balance sheet total**")
        c1, c2 = st.columns(2)
        with c1:
            northdata_balance_sheet_total_min = money_input(
                "Minimum Northdata balance sheet total EUR",
                "export_northdata_balance_sheet_total_min",
            )
        with c2:
            northdata_balance_sheet_total_max = money_input(
                "Maximum Northdata balance sheet total EUR",
                "export_northdata_balance_sheet_total_max",
            )

        c1, c2 = st.columns(2)
        with c1:
            openregister_balance_sheet_total_min = money_input(
                "Minimum OpenRegister balance sheet total EUR",
                "export_openregister_balance_sheet_total_min",
            )
        with c2:
            openregister_balance_sheet_total_max = money_input(
                "Maximum OpenRegister balance sheet total EUR",
                "export_openregister_balance_sheet_total_max",
            )

        st.markdown("**Equity**")
        c1, c2 = st.columns(2)
        with c1:
            northdata_equity_min = money_input(
                "Minimum Northdata equity EUR",
                "export_northdata_equity_min",
            )
        with c2:
            northdata_equity_max = money_input(
                "Maximum Northdata equity EUR",
                "export_northdata_equity_max",
            )

        c1, c2 = st.columns(2)
        with c1:
            openregister_equity_min = money_input(
                "Minimum OpenRegister equity EUR",
                "export_openregister_equity_min",
            )
        with c2:
            openregister_equity_max = money_input(
                "Maximum OpenRegister equity EUR",
                "export_openregister_equity_max",
            )

        st.markdown("**Net income**")
        c1, c2 = st.columns(2)
        with c1:
            northdata_net_income_min = money_input(
                "Minimum Northdata net income EUR",
                "export_northdata_net_income_min",
            )
        with c2:
            northdata_net_income_max = money_input(
                "Maximum Northdata net income EUR",
                "export_northdata_net_income_max",
            )

        c1, c2 = st.columns(2)
        with c1:
            openregister_net_income_min = money_input(
                "Minimum OpenRegister net income EUR",
                "export_openregister_net_income_min",
            )
        with c2:
            openregister_net_income_max = money_input(
                "Maximum OpenRegister net income EUR",
                "export_openregister_net_income_max",
            )

        c1, c2 = st.columns(2)
        with c1:
            shareholder_age_min = int_input("Minimum shareholder age", "export_shareholder_age_min", min_value=0)
        with c2:
            shareholder_age_max = int_input("Maximum shareholder age", "export_shareholder_age_max", min_value=0)

        c1, c2 = st.columns(2)
        with c1:
            total_shareholders_min = int_input("Minimum total shareholders", "export_total_shareholders_min", min_value=0)
        with c2:
            total_shareholders_max = int_input("Maximum total shareholders", "export_total_shareholders_max", min_value=0)

        c1, c2 = st.columns(2)
        with c1:
            legal_shareholders_min = int_input("Minimum legal shareholders", "export_legal_shareholders_min", min_value=0)
        with c2:
            legal_shareholders_max = int_input("Maximum legal shareholders", "export_legal_shareholders_max", min_value=0)

        c1, c2 = st.columns(2)
        with c1:
            natural_shareholders_min = int_input("Minimum natural shareholders", "export_natural_shareholders_min", min_value=0)
        with c2:
            natural_shareholders_max = int_input("Maximum natural shareholders", "export_natural_shareholders_max", min_value=0)

        submitted = st.form_submit_button("Generate filtered workbook", type="primary")

    if submitted:
        validation_errors: list[str] = []

        validate_min_max("Fit score", fit_score_min, fit_score_max, validation_errors)
        validate_min_max("Northdata employees", northdata_employees_min, northdata_employees_max, validation_errors)
        validate_min_max("OpenRegister employees", openregister_employees_min, openregister_employees_max, validation_errors)
        validate_min_max("Northdata revenue", northdata_revenue_min, northdata_revenue_max, validation_errors)
        validate_min_max("OpenRegister revenue", openregister_revenue_min, openregister_revenue_max, validation_errors)
        validate_min_max(
            "Northdata balance sheet total",
            northdata_balance_sheet_total_min,
            northdata_balance_sheet_total_max,
            validation_errors,
        )
        validate_min_max(
            "OpenRegister balance sheet total",
            openregister_balance_sheet_total_min,
            openregister_balance_sheet_total_max,
            validation_errors,
        )
        validate_min_max("Northdata equity", northdata_equity_min, northdata_equity_max, validation_errors)
        validate_min_max("OpenRegister equity", openregister_equity_min, openregister_equity_max, validation_errors)
        validate_min_max("Northdata net income", northdata_net_income_min, northdata_net_income_max, validation_errors)
        validate_min_max("OpenRegister net income", openregister_net_income_min, openregister_net_income_max, validation_errors)
        validate_min_max("Shareholder age", shareholder_age_min, shareholder_age_max, validation_errors)
        validate_min_max("Total shareholders", total_shareholders_min, total_shareholders_max, validation_errors)
        validate_min_max("Legal shareholders", legal_shareholders_min, legal_shareholders_max, validation_errors)
        validate_min_max("Natural shareholders", natural_shareholders_min, natural_shareholders_max, validation_errors)

        if validation_errors:
            st.error("Fix these filter errors before generating the workbook:")

            for err in validation_errors:
                st.write(f"- {err}")

            return

        try:
            rows = fetch_all_rows_paginated(supabase, "master_overview")
            df = pd.DataFrame(rows)

            if df.empty:
                st.warning("No data found in master_overview.")
                return

            filters = {
                "legal_form_terms": parse_csv_values(legal_forms_text),
                "northdata_wz_terms": parse_csv_values(northdata_wz_text),
                "shareholder_age_min": shareholder_age_min,
                "shareholder_age_max": shareholder_age_max,
                "ranges": [
                    {"column": "fit_score", "min": fit_score_min, "max": fit_score_max},

                    {"column": "northdata_employees", "min": northdata_employees_min, "max": northdata_employees_max},
                    {"column": "openregister_employees", "min": openregister_employees_min, "max": openregister_employees_max},

                    {"column": "northdata_revenue_eur", "min": northdata_revenue_min, "max": northdata_revenue_max},
                    {"column": "openregister_revenue_eur", "min": openregister_revenue_min, "max": openregister_revenue_max},

                    {
                        "column": "northdata_balance_sheet_total_eur",
                        "min": northdata_balance_sheet_total_min,
                        "max": northdata_balance_sheet_total_max,
                    },
                    {
                        "column": "openregister_balance_sheet_total_eur",
                        "min": openregister_balance_sheet_total_min,
                        "max": openregister_balance_sheet_total_max,
                    },

                    {"column": "northdata_equity_eur", "min": northdata_equity_min, "max": northdata_equity_max},
                    {"column": "openregister_equity_eur", "min": openregister_equity_min, "max": openregister_equity_max},

                    {"column": "northdata_net_income_eur", "min": northdata_net_income_min, "max": northdata_net_income_max},
                    {"column": "openregister_net_income_eur", "min": openregister_net_income_min, "max": openregister_net_income_max},

                    {"column": "number_of_shareholders", "min": total_shareholders_min, "max": total_shareholders_max},
                    {
                        "column": "legal_person_shareholder_count",
                        "min": legal_shareholders_min,
                        "max": legal_shareholders_max,
                    },
                    {
                        "column": "natural_person_shareholder_count",
                        "min": natural_shareholders_min,
                        "max": natural_shareholders_max,
                    },
                ],
            }

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
        st.info(
            "Supabase and Google Sheets credentials come from Streamlit secrets. "
            "OpenRegister and Claude keys are pasted here."
        )

    try:
        supabase = get_supabase_client()
    except Exception as exc:
        st.error(f"Supabase connection failed: {exc}")
        st.stop()

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
