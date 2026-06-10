from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.google_sheets_sync import sync_supabase_to_google_sheets
from modules.openregister_enrichment import run_enrichment
from modules.openregister_search import run_company_search
from modules.supabase_client import get_supabase_client
from modules.utils import parse_csv_values

st.set_page_config(page_title="Succession Analysis OpenRegister", page_icon="📊", layout="wide")

LEGAL_FORM_OPTIONS = {
    "GmbH": "gmbh",
    "UG": "ug",
    "gGmbH": "ggmbh",
    "GmbH & Co. KG / KG": "kg",
    "OHG": "ohg",
    "e.K.": "ek",
    "GbR": "gbr",
    "AG": "ag",
    "SE": "se",
    "KGaA": "kgaa",
    "eG": "eg",
    "e.V.": "ev",
    "EWIV": "ewiv",
    "Foreign": "foreign",
    "LLP": "llp",
    "Municipal": "municipal",
}
DEFAULT_LEGAL_FORMS = {"GmbH", "UG", "gGmbH", "GmbH & Co. KG / KG", "OHG", "e.K.", "GbR"}

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


def bool_filter(label: str, key: str):
    value = st.selectbox(label, ["Any", "Yes", "No"], key=key)
    if value == "Yes":
        return True
    if value == "No":
        return False
    return None


def get_backend_counts(supabase) -> dict[str, int | str]:
    tables = {
        "Companies": "companies",
        "Financial rows": "company_financials",
        "Owner rows": "shareholders",
        "UBO rows": "company_ubos",
        "Logs": "processing_logs",
    }
    counts = {}
    for label, table in tables.items():
        try:
            res = supabase.table(table).select("id", count="exact").limit(1).execute()
            counts[label] = getattr(res, "count", 0)
        except Exception as exc:
            counts[label] = f"Error: {exc}"
    return counts


def fetch_table(supabase, table: str, limit: int = 200) -> pd.DataFrame:
    res = supabase.table(table).select("*").limit(limit).execute()
    rows = getattr(res, "data", None) or []
    return pd.DataFrame(rows)


def search_tab(supabase, openregister_api_key: str):
    st.header("OpenRegister Filter Search")
    st.caption("Search companies directly in OpenRegister and save matched companies to Supabase. Companies are deduped by OpenRegister company ID.")

    with st.form("openregister_filter_search"):
        search_name = st.text_input("Search name", value="Succession target search")
        max_companies = st.number_input("Max companies to fetch", min_value=1, max_value=5000, value=100, step=25)

        st.subheader("Company filters")
        active_only = st.checkbox("Active companies only", value=True)

        st.write("Legal forms")
        legal_forms = []
        cols = st.columns(4)
        for i, (label, value) in enumerate(LEGAL_FORM_OPTIONS.items()):
            with cols[i % 4]:
                if st.checkbox(label, value=label in DEFAULT_LEGAL_FORMS, key=f"legal_form_{value}"):
                    legal_forms.append(value)

        industry_codes_text = st.text_input("Industry codes", placeholder="Example: 25.62, 28.41, 62.01")
        purpose_text = st.text_input("Business purpose keywords", placeholder="Example: Maschinenbau, Software, Pflege")
        has_lei = bool_filter("Has LEI", "has_lei_filter")

        st.subheader("Financial / company-size filters")
        financial_config = {}
        for idx, (field, label) in enumerate(FINANCIAL_FIELDS):
            c1, c2 = st.columns(2)
            with c1:
                financial_config[f"{field}_min"] = st.number_input(f"{label} min", min_value=0.0, value=0.0, step=1000.0, key=f"{field}_min")
            with c2:
                financial_config[f"{field}_max"] = st.number_input(f"{label} max", min_value=0.0, value=0.0, step=1000.0, key=f"{field}_max")

        st.subheader("Ownership / succession filters")
        c1, c2 = st.columns(2)
        with c1:
            number_of_owners_min = st.number_input("Number of owners min", min_value=0, value=0, step=1)
            youngest_owner_age_min = st.number_input("Youngest owner age min", min_value=0, value=0, step=1)
        with c2:
            number_of_owners_max = st.number_input("Number of owners max", min_value=0, value=0, step=1)
            youngest_owner_age_max = st.number_input("Youngest owner age max", min_value=0, value=0, step=1)

        c1, c2, c3 = st.columns(3)
        with c1:
            has_sole_owner = bool_filter("Has sole owner", "has_sole_owner_filter")
        with c2:
            has_representative_owner = bool_filter("Owner-managed", "has_representative_owner_filter")
        with c3:
            is_family_owned = bool_filter("Family-owned", "is_family_owned_filter")

        submitted = st.form_submit_button("Run search and save companies", type="primary")

    if submitted:
        if not openregister_api_key:
            st.error("Paste your OpenRegister API key in the sidebar first.")
            return
        config = {
            "active_only": active_only,
            "legal_forms": legal_forms,
            "industry_codes": parse_csv_values(industry_codes_text),
            "purpose_keywords": parse_csv_values(purpose_text),
            "has_lei": has_lei,
            **{k: (None if v == 0 else v) for k, v in financial_config.items()},
            "number_of_owners_min": None if number_of_owners_min == 0 else number_of_owners_min,
            "number_of_owners_max": None if number_of_owners_max == 0 else number_of_owners_max,
            "youngest_owner_age_min": None if youngest_owner_age_min == 0 else youngest_owner_age_min,
            "youngest_owner_age_max": None if youngest_owner_age_max == 0 else youngest_owner_age_max,
            "has_sole_owner": has_sole_owner,
            "has_representative_owner": has_representative_owner,
            "is_family_owned": is_family_owned,
        }
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
            st.json(result["filters"])
            if result["rows"]:
                st.dataframe(pd.DataFrame(result["rows"]), use_container_width=True)
        else:
            st.error(result.get("error", "Search failed."))
            st.json(result.get("filters", []))


def enrichment_tab(supabase, openregister_api_key: str):
    st.header("Enrichment")
    st.caption("Run selected OpenRegister enrichment endpoints for companies already saved in Supabase.")

    c1, c2 = st.columns(2)
    with c1:
        limit = st.number_input("Max backend companies to enrich", min_value=1, max_value=5000, value=50, step=25)
        existing_behavior = st.radio("Existing enrichment behavior", ["Skip existing", "Update existing"], horizontal=True)
    with c2:
        fetch_company_info = st.checkbox("Company info", value=True)
        fetch_financials = st.checkbox("Financials", value=True)
        fetch_ownership = st.checkbox("Ownership", value=True)
        fetch_ubos = st.checkbox("UBOs", value=False)
        best_available_owners = st.checkbox("Use best-available ownership for AG/SE when needed", value=False)

    if st.button("Run enrichment", type="primary"):
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
                limit=int(limit),
                update_existing=existing_behavior == "Update existing",
                fetch_company_info=fetch_company_info,
                fetch_financials=fetch_financials,
                fetch_ownership=fetch_ownership,
                fetch_ubos=fetch_ubos,
                best_available_owners=best_available_owners,
            )
        st.success(f"Enrichment finished for {result['companies_seen']} backend companies.")
        if result["results"]:
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)


def backend_tab(supabase):
    st.header("Backend Data")
    counts = get_backend_counts(supabase)
    cols = st.columns(len(counts))
    for col, (label, value) in zip(cols, counts.items()):
        col.metric(label, value)

    table = st.selectbox(
        "View table/view",
        ["master_overview", "companies", "company_financials", "shareholders", "company_ubos", "openregister_search_runs", "processing_logs"],
    )
    limit = st.number_input("Rows to show", min_value=10, max_value=5000, value=200, step=50)
    if st.button("Load data"):
        with st.spinner("Loading from Supabase..."):
            df = fetch_table(supabase, table, int(limit))
        st.dataframe(df, use_container_width=True)


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


def main():
    st.title("Succession Analysis — OpenRegister")
    st.caption("OpenRegister search → Supabase backend → enrichment → Google Sheets")

    with st.sidebar:
        st.header("Configuration")
        openregister_api_key = st.text_input("OpenRegister API key", type="password")
        st.info("Supabase and Google Sheets credentials come from Streamlit secrets. OpenRegister key is pasted here.")

    try:
        supabase = get_supabase_client()
    except Exception as exc:
        st.error(f"Supabase connection failed: {exc}")
        st.stop()

    tab_search, tab_enrich, tab_backend, tab_sheets = st.tabs([
        "Filter Search",
        "Enrichment",
        "Backend Data",
        "Google Sheets Sync",
    ])

    with tab_search:
        search_tab(supabase, openregister_api_key)
    with tab_enrich:
        enrichment_tab(supabase, openregister_api_key)
    with tab_backend:
        backend_tab(supabase)
    with tab_sheets:
        sheets_tab(supabase)


if __name__ == "__main__":
    main()
