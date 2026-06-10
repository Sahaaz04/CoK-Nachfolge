from __future__ import annotations

from datetime import date
from typing import Any


def safe_to_dict(value: Any) -> Any:
    """Convert Stainless/Pydantic models into plain JSON-safe objects."""
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [safe_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: safe_to_dict(item) for key, item in value.items()}
    if isinstance(value, (date,)):
        return value.isoformat()
    return value


def euro_to_cents(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        return str(int(round(float(value) * 100)))
    except Exception:
        return None


def number_to_api_string(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        if float(value).is_integer():
            return str(int(value))
        return str(value)
    except Exception:
        return None


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def make_owner_age_from_dob(date_of_birth: str | None) -> int | None:
    if not date_of_birth:
        return None
    try:
        year, month, day = [int(part) for part in date_of_birth[:10].split("-")]
        today = date.today()
        return today.year - year - ((today.month, today.day) < (month, day))
    except Exception:
        return None
