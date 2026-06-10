from __future__ import annotations

import streamlit as st
from openregister import Openregister


class MissingOpenRegisterConfig(RuntimeError):
    pass


@st.cache_resource(show_spinner=False)
def get_openregister_client(api_key: str | None = None) -> Openregister:
    """Create cached OpenRegister SDK client.

    OpenRegister API key is intentionally supplied from the Streamlit UI,
    not from secrets.toml. Supabase/Google credentials stay in secrets.
    """
    if not api_key:
        raise MissingOpenRegisterConfig("Paste your OpenRegister API key in the app sidebar before running search/enrichment.")
    return Openregister(api_key=api_key, max_retries=2, timeout=60.0)
