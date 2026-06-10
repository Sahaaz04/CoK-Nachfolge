from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.openregister_search import (
    DEFAULT_LEGAL_FORM_LABELS,
    LEGAL_FORM_OPTIONS,
    build_company_search_filters,
    parse_codes_from_text,
    parse_keywords_from_text,
    run_company_search_and_save,
)
from modules.supabase_client import fetch_recent_companies, get_supabase_client


st.set_page_config(page_title="Succession Analysis — OpenRegister", layout="wide")

st.title("Succession Analysis — OpenRegister FRESH BUILD v0.1")
st.caption("OpenRegister-first search → Supabase backend → enrichment later → Google Sheets later")


def tri_state_select(label: str, help_text: str | None = None) -> bool | None:
    value = st.selectbox(label, ["Any", "Yes", "No"], help=help_text)
    if value == "Yes":
        return True
    if value == "No":
        return False
    return None


def range_inputs(label: str, *, money: bool = False, integer: bool = False) -> tuple[float | int | None, float | int | None]:
    c1, c2 = st.columns(2)
    step = 1 if integer else 1000 if money else 1
    number_format = "%d" if integer else None
    with c1:
        min_val = st.number_input(
            f"{label} min",
            min_value=0,
            value=None,
            step=step,
            format=number_format,
            placeholder="No minimum",
        )
    with c2:
        max_val = st.number_input(
            f"{label} max",
            min_value=0,
            value=None,
            step=step,
            format=number_format,
            placeholder="No maximum",
        )
    return min_val, max_val


with st.sidebar:
    st.header("Configuration")
    openregister_api_key = st.text_input(
        "OpenRegister API key",
        type="password",
        help="Paste the OpenRegister API key for this run. It is not stored in secrets.",
    )
    st.info("Use your new Supabase and Google Sheets keys in Streamlit secrets. OpenRegister key is pasted here in the app.")

try:
    supabase = get_supabase_client()
    supabase_ok = True
except Exception as exc:
    supabase = None
    supabase_ok = False
    st.error(str(exc))

search_tab, companies_tab, notes_tab = st.tabs(["1. Filter Search", "2. Backend Companies", "Notes"])

with search_tab:
    st.subheader("OpenRegister Advanced Company Search")
    st.write("This saves matched companies into Supabase. Each company is deduped by `openregister_company_id`.")

    with st.form("openregister_filter_search_form"):
        st.markdown("### Basic company filters")
        c1, c2 = st.columns([1, 2])
        with c1:
            active_only = st.checkbox("Active companies only", value=True)
        with c2:
            selected_legal_labels = st.multiselect(
                "Legal forms",
                options=list(LEGAL_FORM_OPTIONS.keys()),
                default=DEFAULT_LEGAL_FORM_LABELS,
                help="Tick-box style filter. These map to OpenRegister legal_form values.",
            )

        c1, c2 = st.columns(2)
        with c1:
            industry_codes_text = st.text_input(
                "Industry codes",
                placeholder="Example: 56.11, 62.01",
                help="Comma-separated OpenRegister/WZ industry codes. Leave blank for any industry.",
            )
        with c2:
            purpose_keywords_text = st.text_input(
                "Business purpose keywords",
                placeholder="Example: Maschinenbau, Pflege, Software",
                help="Comma-separated keywords searched in the registered company purpose text.",
            )

        c1, c2 = st.columns(2)
        with c1:
            query_text = st.text_input(
                "Optional company text query",
                placeholder="Optional. Usually blank for pure filter search.",
            )
        with c2:
            has_lei = tri_state_select(
                "Has LEI",
                help_text="Usually keep Any. LEI is mostly relevant for financial-market/compliance entities.",
            )

        st.markdown("### Financial / company size filters")
        st.caption("Enter money values in EUR. The backend converts EUR → cents for OpenRegister.")
        f1, f2, f3 = st.columns(3)
        with f1:
            revenue_range = range_inputs("Revenue EUR", money=True)
            net_income_range = range_inputs("Net income EUR", money=True)
            liabilities_range = range_inputs("Liabilities EUR", money=True)
        with f2:
            employees_range = range_inputs("Employees", integer=True)
            equity_range = range_inputs("Equity EUR", money=True)
            real_estate_range = range_inputs("Real estate EUR", money=True)
        with f3:
            balance_sheet_total_range = range_inputs("Balance sheet total EUR", money=True)
            cash_range = range_inputs("Cash EUR", money=True)
            capital_amount_range = range_inputs("Capital amount EUR", money=True)

        st.markdown("### Ownership / succession filters")
        o1, o2, o3 = st.columns(3)
        with o1:
            number_of_owners_range = range_inputs("Number of owners", integer=True)
        with o2:
            youngest_owner_age_range = range_inputs("Youngest owner age", integer=True)
        with o3:
            has_sole_owner = tri_state_select("Has sole owner")
            has_representative_owner = tri_state_select("Owner-managed / representative owner")
            is_family_owned = tri_state_select("Is family-owned")

        st.markdown("### Run controls")
        c1, c2, c3 = st.columns(3)
        with c1:
            search_name = st.text_input("Search run name", value="OpenRegister succession search")
        with c2:
            max_companies = st.number_input("Max companies to fetch", min_value=1, max_value=1000, value=100, step=10)
        with c3:
            per_page = st.number_input("Results per API page", min_value=1, max_value=100, value=100, step=10)

        submitted = st.form_submit_button("Run search and save companies", type="primary")

    if submitted:
        if not supabase_ok or supabase is None:
            st.error("Supabase is not configured yet.")
        else:
            legal_values = [LEGAL_FORM_OPTIONS[label] for label in selected_legal_labels]
            financial_ranges = {
                "revenue": revenue_range,
                "employees": employees_range,
                "balance_sheet_total": balance_sheet_total_range,
                "net_income": net_income_range,
                "equity": equity_range,
                "cash": cash_range,
                "liabilities": liabilities_range,
                "real_estate": real_estate_range,
                "capital_amount": capital_amount_range,
            }
            ownership_ranges = {
                "number_of_owners": number_of_owners_range,
                "youngest_owner_age": youngest_owner_age_range,
            }
            ownership_booleans = {
                "has_sole_owner": has_sole_owner,
                "has_representative_owner": has_representative_owner,
                "is_family_owned": is_family_owned,
            }
            filters = build_company_search_filters(
                active_only=active_only,
                legal_form_values=legal_values,
                industry_codes=parse_codes_from_text(industry_codes_text),
                purpose_keywords=parse_keywords_from_text(purpose_keywords_text),
                has_lei=has_lei,
                financial_ranges=financial_ranges,
                ownership_ranges=ownership_ranges,
                ownership_booleans=ownership_booleans,
            )

            with st.expander("Filters sent to OpenRegister", expanded=False):
                st.json(filters)

            with st.spinner("Searching OpenRegister and saving companies to Supabase..."):
                summary = run_company_search_and_save(
                    supabase=supabase,
                    api_key_override=openregister_api_key or None,
                    search_name=search_name,
                    filters=filters,
                    query_text=query_text or None,
                    max_companies=int(max_companies),
                    per_page=int(per_page),
                )

            if summary.errors:
                st.error("\n".join(summary.errors))
            else:
                st.success(
                    f"Search saved. Returned {summary.returned_companies} companies; "
                    f"upserted {summary.saved_companies} company rows."
                )
                if summary.search_run_id:
                    st.caption(f"Search run ID: {summary.search_run_id}")
                if summary.companies:
                    df = pd.DataFrame(summary.companies)
                    display_cols = [
                        col
                        for col in [
                            "openregister_company_id",
                            "name",
                            "legal_form",
                            "active",
                            "country",
                            "register_court",
                            "register_number",
                            "register_type",
                        ]
                        if col in df.columns
                    ]
                    st.dataframe(df[display_cols], use_container_width=True)

with companies_tab:
    st.subheader("Recently saved companies")
    if not supabase_ok:
        st.warning("Supabase is not configured yet.")
    elif st.button("Refresh companies"):
        rows = fetch_recent_companies(100)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("No companies found yet.")
    else:
        rows = fetch_recent_companies(25) if supabase_ok else []
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("No companies found yet. Run a filter search first.")

with notes_tab:
    st.subheader("Current build status")
    st.markdown(
        """
        **Done in this step**

        - Clean from-scratch app structure
        - New secrets template for your new Supabase and Google Sheets project
        - Supabase connection module
        - OpenRegister client module
        - Advanced filter search form
        - Company upsert into Supabase using `openregister_company_id`

        **Next step**

        - Add enrichment checkboxes: company info, financials, ownership, UBOs
        - Add skip/update existing behavior
        - Save enrichment data into child tables
        """
    )
