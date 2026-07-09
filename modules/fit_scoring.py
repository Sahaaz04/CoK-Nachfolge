from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from anthropic import Anthropic

DEFAULT_FIT_CONFIG = {
    "revenue_min": 4000000,
    "revenue_max": 8000000,
    "employees_min": 20,
    "employees_max": 1000,
    "equity_ratio_min": 15,
    "equity_ratio_good": 30,
    "min_shareholder_age": 55,
    "preferred_business_type": "B2B industrial company",
    "preferred_industries": "cosmetics, food, contract manufacturing",
    "profit_proxy_target": "EBITDA under/around EUR 400k or weak/stagnating profitability may indicate upside if the business is otherwise stable",
    "additional_instructions": "Prioritize succession situations, simple ownership, industrial/B2B production, and companies with clear operational improvement potential.",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _yes_no_flag(value: Any) -> str:
    text = safe(value)
    lowered = text.lower()

    if lowered in {"yes", "y", "true", "1"}:
        return "Yes"

    if lowered in {"no", "n", "false", "0"}:
        return "No"

    return ""


def _claude_assumption_flag(model_row: dict[str, Any], company: dict[str, Any]) -> str:
    explicit = _yes_no_flag(
        model_row.get("business_segment_2")
        or company.get("claude_assumption")
        or company.get("business_segment_2")
        or company.get("claude_business_segment_2")
    )

    if explicit:
        return explicit

    status = safe(model_row.get("api_status") or company.get("claude_api_status")).upper()

    if status.startswith("FALLBACK") or "FALLBACK" in status:
        return "Yes"

    if model_row or company.get("claude_business_segment"):
        return "No"

    return ""


def _fetch_all_paginated(
    supabase,
    table: str,
    select: str = "*",
    page_size: int = 1000,
    hard_cap: int = 50000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0

    while len(rows) < hard_cap:
        end = min(start + page_size - 1, hard_cap - 1)

        res = supabase.table(table).select(select).range(start, end).execute()
        batch = getattr(res, "data", None) or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return rows


def _fetch_rows(
    supabase,
    table: str,
    column: str,
    value: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    res = supabase.table(table).select("*").eq(column, value).limit(limit).execute()
    return getattr(res, "data", None) or []


def _latest_model(supabase, register_id: str, company_id: str | None) -> dict[str, Any]:
    rows = _fetch_rows(
        supabase,
        "company_models",
        "company_register_id",
        register_id,
        limit=20,
    )

    if not rows and company_id:
        rows = _fetch_rows(
            supabase,
            "company_models",
            "openregister_company_id",
            company_id,
            limit=20,
        )

    rows = [r for r in rows if r.get("model_provider") == "claude"]

    if not rows:
        return {}

    return sorted(
        rows,
        key=lambda r: safe(r.get("updated_at") or r.get("created_at")),
        reverse=True,
    )[0]


def _existing_score_exists(supabase, register_id: str) -> bool:
    res = (
        supabase.table("company_fit_scores")
        .select("id")
        .eq("company_register_id", register_id)
        .eq("model_provider", "claude")
        .limit(1)
        .execute()
    )

    return bool(getattr(res, "data", None) or [])


def _delete_existing_score(supabase, register_id: str) -> None:
    (
        supabase.table("company_fit_scores")
        .delete()
        .eq("company_register_id", register_id)
        .eq("model_provider", "claude")
        .execute()
    )


def _summarize_shareholders(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shareholders: list[dict[str, Any]] = []

    for row in rows[:20]:
        shareholders.append({
            "name": row.get("shareholder_name"),
            "type": row.get("owner_type"),
            "relation_type": row.get("relation_type"),
            "age": row.get("age"),
            "nominal_share_eur": row.get("nominal_share_eur"),
            "ownership_percentage": row.get("percentage_share"),
            "city": row.get("owner_city"),
            "country": row.get("owner_country"),
        })

    ages = [r.get("age") for r in rows if r.get("age") is not None]

    return {
        "total": len(rows),
        "natural_person_count": sum(1 for r in rows if r.get("owner_type") == "natural_person"),
        "legal_person_count": sum(1 for r in rows if r.get("owner_type") == "legal_person"),
        "youngest_age": min(ages) if ages else None,
        "oldest_age": max(ages) if ages else None,
        "shareholders": shareholders,
    }


def _summarize_ubos(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ubos: list[dict[str, Any]] = []

    for row in rows[:20]:
        ubos.append({
            "name": row.get("ubo_name"),
            "type": row.get("ubo_type"),
            "age": row.get("age"),
            "percentage_share": row.get("percentage_share"),
            "max_percentage_share": row.get("max_percentage_share"),
            "city": row.get("ubo_city"),
            "country": row.get("ubo_country"),
        })

    ages = [r.get("age") for r in rows if r.get("age") is not None]

    return {
        "total": len(rows),
        "natural_person_count": sum(1 for r in rows if r.get("ubo_type") == "natural_person"),
        "legal_person_count": sum(1 for r in rows if r.get("ubo_type") == "legal_person"),
        "youngest_age": min(ages) if ages else None,
        "oldest_age": max(ages) if ages else None,
        "ubos": ubos,
    }


def build_fit_score_prompt(
    company: dict[str, Any],
    model_row: dict[str, Any],
    shareholders: list[dict[str, Any]],
    ubos: list[dict[str, Any]],
    fit_config: dict[str, Any],
) -> str:
    company_payload = {
        "identity": {
            "register_id": company.get("register_id"),
            "openregister_company_id": company.get("openregister_company_id"),
            "company_name": company.get("company_name") or company.get("name"),
            "legal_form": company.get("legal_form"),
            "founding_year": company.get("founding_year"),
            "active": company.get("active"),
            "country": company.get("country"),
            "city": company.get("city"),
            "postal_code": company.get("postal_code"),
            "website": company.get("website"),
        },

        "business": {
            # NorthData business model is source-specific.
            # OpenRegister purpose is internal context only and may not be present
            # in master_overview unless the view is extended later.
            "northdata_business_model": company.get("northdata_business_model"),
            "openregister_purpose": company.get("openregister_purpose"),

            "northdata_wz_code": company.get("northdata_wz_code"),
            "openregister_wz_codes": company.get("openregister_wz_codes"),

            "claude_business_segment": (
                model_row.get("business_segment")
                or company.get("claude_business_segment")
            ),
            "claude_assumption": _claude_assumption_flag(model_row, company),
            "claude_business_model": (
                model_row.get("business_model")
                or company.get("claude_business_model")
            ),
            "claude_business_summary": (
                model_row.get("summary")
                or company.get("claude_detailed_business_summary")
            ),
        },

        "financials": {
            # Revenue is source-specific.
            "northdata_revenue_eur": company.get("northdata_revenue_eur"),
            "openregister_revenue_eur": company.get("openregister_revenue_eur"),

            # Employees are source-specific.
            "northdata_employees": company.get("northdata_employees"),
            "openregister_employees": company.get("openregister_employees"),

            # Balance sheet total is source-specific.
            "northdata_balance_sheet_total_eur": company.get("northdata_balance_sheet_total_eur"),
            "openregister_balance_sheet_total_eur": company.get("openregister_balance_sheet_total_eur"),

            # Net income is source-specific.
            "northdata_net_income_eur": company.get("northdata_net_income_eur"),
            "openregister_net_income_eur": company.get("openregister_net_income_eur"),

            # Equity is source-specific.
            "northdata_equity_eur": company.get("northdata_equity_eur"),
            "openregister_equity_eur": company.get("openregister_equity_eur"),

            # Cash is source-specific.
            "northdata_cash_eur": company.get("northdata_cash_eur"),
            "openregister_cash_eur": company.get("openregister_cash_eur"),

            # Liabilities are source-specific.
            "northdata_liabilities_eur": company.get("northdata_liabilities_eur"),
            "openregister_liabilities_eur": company.get("openregister_liabilities_eur"),

            "northdata_financials_date": company.get("northdata_financials_date"),
            "openregister_financials_date": company.get("openregister_financials_date"),
        },

        "shareholder_summary": {
            "number_of_shareholders": company.get("number_of_shareholders"),
            "natural_person_shareholder_count": company.get("natural_person_shareholder_count"),
            "legal_person_shareholder_count": company.get("legal_person_shareholder_count"),
            "youngest_shareholder_age": company.get("youngest_shareholder_age"),
            "oldest_shareholder_age": company.get("oldest_shareholder_age"),
            "largest_shareholder_name": company.get("shareholder_name"),
            "largest_shareholder_type": company.get("shareholder_type"),
            "largest_shareholder_age": company.get("shareholder_age"),
            "largest_shareholder_ownership_percentage": company.get("shareholder_ownership_percentage"),
        },

        "ubo_summary": {
            "ubo_count": company.get("ubo_count"),
            "youngest_ubo_age": company.get("youngest_ubo_age"),
            "oldest_ubo_age": company.get("oldest_ubo_age"),
            "largest_ubo_name": company.get("ubo_name"),
            "largest_ubo_type": company.get("ubo_type"),
            "largest_ubo_age": company.get("ubo_age"),
            "largest_ubo_percentage_share": company.get("ubo_percentage_share"),
            "largest_ubo_max_percentage_share": company.get("ubo_max_percentage_share"),
        },

        "direct_shareholders": _summarize_shareholders(shareholders),
        "beneficial_ownership_or_control_chain": _summarize_ubos(ubos),
        "target_criteria": fit_config,
    }

    return f"""
You are scoring German companies for acquisition / succession fit.

Use ONLY the provided company data. Do not invent facts.

Score from 1 to 5:
5 = Very high fit: most criteria fulfilled, strong succession/acquisition potential, healthy or improvable company.
4 = High fit: key criteria fulfilled, succession/acquisition potential visible.
3 = Medium fit: some criteria fit, but important gaps or uncertainty.
2 = Low fit: major criteria missing, weak fit.
1 = No fit: clearly outside target profile or high-risk.

Important scoring guidance:
- Revenue, employee min/max, preferred industries, business type, shareholder age and profitability targets are driven by user config.
- founding_year is the company's own founding/incorporation year. It is not a shareholder integration year.
- An older founding_year can indicate company maturity, operating history, and possible succession relevance, but do not over-weight it without ownership or management evidence.

Financial source rules:
- All financial fields are source-specific.
- Do not merge NorthData and OpenRegister values.
- Do not copy, average, or fallback from one source into the other.
- If only one source has a value, say which source has it.
- If both sources exist and differ, mention the discrepancy as uncertainty when relevant.
- If NorthData values are missing but OpenRegister values exist, treat that as OpenRegister-only financial evidence.

Revenue fields:
- northdata_revenue_eur is NorthData revenue.
- openregister_revenue_eur is OpenRegister revenue.

Employee fields:
- northdata_employees is NorthData employee count.
- openregister_employees is OpenRegister employee count.

Balance sheet fields:
- northdata_balance_sheet_total_eur is NorthData balance sheet total.
- openregister_balance_sheet_total_eur is OpenRegister balance sheet total.

Net income fields:
- northdata_net_income_eur is NorthData net income.
- openregister_net_income_eur is OpenRegister net income.

Equity fields:
- northdata_equity_eur is NorthData equity.
- openregister_equity_eur is OpenRegister equity.

Cash fields:
- northdata_cash_eur is NorthData cash.
- openregister_cash_eur is OpenRegister cash.

Liabilities fields:
- northdata_liabilities_eur is NorthData liabilities.
- openregister_liabilities_eur is OpenRegister liabilities.

Business / industry source rules:
- northdata_business_model is from NorthData upload.
- openregister_purpose is the registered OpenRegister purpose, if available.
- northdata_wz_code is from NorthData.
- openregister_wz_codes is from OpenRegister.
- Do not merge WZ fields.
- If NorthData WZ is missing, do not infer that the company was confirmed by NorthData.

Claude business fields:
- claude_business_segment is the final official division label, either website-derived or fallback-assumed.
- claude_assumption = "No" means Claude segment, model, and summary were derived from website evidence.
- claude_assumption = "Yes" means Claude segment, model, and summary were fallback assumptions from source-separated business context because website evidence was unavailable, scraping failed, or website analysis was incomplete.
- Treat claude_business_model as stronger evidence when claude_assumption is "No".
- Treat claude_business_model as weaker/conservative evidence when claude_assumption is "Yes".
- Do not penalize a company only because claude_assumption is "Yes", but mention uncertainty when it materially affects the score.

Succession / ownership guidance:
- Natural-person direct shareholders or UBOs at/above the configured minimum shareholder age increase succession signal.
- Direct shareholders are the legal ownership layer.
- UBOs are beneficial/control-chain evidence.
- Natural-person ownership is stronger for succession.
- Purely corporate/institutional ownership weakens succession signal.
- Simple ownership is generally better than very complex ownership.
- Use shareholder_summary and ubo_summary first, then direct_shareholders / beneficial_ownership_or_control_chain for detail.

General scoring guidance:
- Positive but not over-optimized profitability can be attractive if operational upside exists.
- Penalize unrelated sectors, distress, missing core data, unclear business model, too-small size, and very complex ownership.
- Prefer Manual Review when the company looks potentially relevant but source discrepancies or missing data prevent a confident score.

Return ONLY valid JSON. No markdown. No explanation outside JSON.

Required JSON schema:
{{
  "fit_score": 1,
  "fit_label": "No Fit / Low Fit / Medium Fit / High Fit / Very High Fit",
  "fit_comment": "2-4 sentence explanation",
  "succession_signal": "short explanation",
  "financial_signal": "short explanation",
  "shareholder_signal": "short explanation",
  "risk_flags": ["flag 1", "flag 2"],
  "recommended_action": "Reject / Monitor / Manual Review / Prioritize"
}}

Company data:
{json.dumps(company_payload, ensure_ascii=False, indent=2, default=str)}
""".strip()


def _parse_json(text: str) -> dict[str, Any]:
    text = safe(text)

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end >= start:
        text = text[start : end + 1]

    return json.loads(text)


def score_with_claude(
    api_key: str,
    model_name: str,
    company: dict[str, Any],
    model_row: dict[str, Any],
    shareholders: list[dict[str, Any]],
    ubos: list[dict[str, Any]],
    fit_config: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    client = Anthropic(api_key=str(api_key).strip())

    request_payload = {
        "model": model_name,
        "max_tokens": 800,
        "messages": [
            {
                "role": "user",
                "content": build_fit_score_prompt(
                    company=company,
                    model_row=model_row,
                    shareholders=shareholders,
                    ubos=ubos,
                    fit_config=fit_config,
                ),
            }
        ],
    }

    if "opus-4-7" not in str(model_name).lower():
        request_payload["temperature"] = 0.1

    response = client.messages.create(**request_payload)

    response_text = "\n".join(
        block.text
        for block in response.content
        if getattr(block, "type", "") == "text"
    ).strip()

    if not response_text:
        raise ValueError("Empty Claude response.")

    return _parse_json(response_text), response_text


def run_fit_scoring(
    *,
    supabase,
    claude_api_key: str,
    model_name: str = "claude-sonnet-4-5",
    fit_config: dict[str, Any] | None = None,
    update_existing: bool = False,
) -> dict[str, Any]:
    if not claude_api_key:
        raise ValueError("Claude API key missing.")

    config = {**DEFAULT_FIT_CONFIG, **(fit_config or {})}
    companies = _fetch_all_paginated(supabase, "master_overview")

    results: list[dict[str, Any]] = []
    processed = 0
    scored = 0
    skipped = 0
    errors = 0

    for company in companies:
        register_id = company.get("register_id")
        company_id = company.get("openregister_company_id")
        company_name = company.get("company_name") or company.get("name") or company_id

        if not register_id:
            continue

        try:
            if _existing_score_exists(supabase, register_id) and not update_existing:
                skipped += 1
                results.append({
                    "company": company_name,
                    "status": "skipped",
                    "reason": "existing score",
                })
                continue

            if update_existing:
                _delete_existing_score(supabase, register_id)

            processed += 1

            model_row = _latest_model(supabase, register_id, company_id)

            shareholders = _fetch_rows(
                supabase,
                "shareholders",
                "company_register_id",
                register_id,
                limit=200,
            )

            ubos = _fetch_rows(
                supabase,
                "company_ubos",
                "company_register_id",
                register_id,
                limit=200,
            )

            parsed, raw_response = score_with_claude(
                api_key=claude_api_key,
                model_name=model_name,
                company=company,
                model_row=model_row,
                shareholders=shareholders,
                ubos=ubos,
                fit_config=config,
            )

            fit_score = parsed.get("fit_score")

            try:
                fit_score = int(fit_score)
            except Exception:
                fit_score = None

            risk_flags = parsed.get("risk_flags", [])
            risk_flags_text = (
                "; ".join(map(str, risk_flags))
                if isinstance(risk_flags, list)
                else safe(risk_flags)
            )

            row = {
                "company_register_id": register_id,
                "openregister_company_id": company_id,
                "company_name": company_name,
                "fit_score": fit_score,
                "fit_label": safe(parsed.get("fit_label")),
                "fit_comment": safe(parsed.get("fit_comment")),
                "succession_signal": safe(parsed.get("succession_signal")),
                "financial_signal": safe(parsed.get("financial_signal")),
                "shareholder_signal": safe(parsed.get("shareholder_signal")),
                "risk_flags": risk_flags_text,
                "recommended_action": safe(parsed.get("recommended_action")),
                "model_provider": "claude",
                "model_name": model_name,
                "scoring_config": config,
                "api_status": "success",
                "notes": "",
                "raw_data": {
                    "parsed": parsed,
                    "raw_response": raw_response,
                },
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            supabase.table("company_fit_scores").upsert(
                row,
                on_conflict="company_register_id,model_provider",
            ).execute()

            scored += 1

            results.append({
                "company": company_name,
                "status": "success",
                "fit_score": fit_score,
                "fit_label": row["fit_label"],
            })

        except Exception as exc:
            errors += 1
            msg = str(exc)[:1000]

            row = {
                "company_register_id": register_id,
                "openregister_company_id": company_id,
                "company_name": company_name,
                "fit_score": None,
                "fit_label": "ERROR",
                "fit_comment": "",
                "succession_signal": "",
                "financial_signal": "",
                "shareholder_signal": "",
                "risk_flags": "",
                "recommended_action": "",
                "model_provider": "claude",
                "model_name": model_name,
                "scoring_config": config,
                "api_status": "error",
                "notes": msg,
                "raw_data": {"error": msg},
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            try:
                supabase.table("company_fit_scores").upsert(
                    row,
                    on_conflict="company_register_id,model_provider",
                ).execute()
            except Exception:
                pass

            results.append({
                "company": company_name,
                "status": "error",
                "error": msg,
            })

    return {
        "companies_seen": len(companies),
        "processed": processed,
        "scored": scored,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }
