from __future__ import annotations

import os
from typing import Any

import streamlit as st
from supabase import Client, create_client


class MissingSupabaseConfig(RuntimeError):
    pass


def _get_secret(name: str) -> str | None:
    """Read from Streamlit secrets first, then environment variables."""
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name)


@st.cache_resource(show_spinner=False)
def get_supabase_client() -> Client:
    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise MissingSupabaseConfig(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY. "
            "Add them to .streamlit/secrets.toml."
        )

    return create_client(url, key)


def fetch_recent_companies(limit: int = 50) -> list[dict[str, Any]]:
    supabase = get_supabase_client()
    response = (
        supabase.table("companies")
        .select("openregister_company_id,name,legal_form,active,country,register_court,register_number,created_at,updated_at")
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []
