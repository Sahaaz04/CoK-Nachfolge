from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup

MAX_WEBSITE_CHARS = 24000
MAX_EXTRA_PAGES = 2
REQUEST_TIMEOUT_SECONDS = 25
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", safe(value)).strip()


def log_event(supabase, **payload: Any) -> None:
    try:
        supabase.table("processing_logs").insert(payload).execute()
    except Exception:
        pass


def normalize_url(url: str | None) -> str:
    text = safe(url)
    if not text:
        return ""
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = "https://" + text
    return text


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SuccessionAnalysisBot/1.0; +https://openai.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS, headers=headers)
    response.raise_for_status()
    return response.text or ""


def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe"]):
        tag.decompose()

    chunks = []
    for elem in soup.find_all(["title", "h1", "h2", "h3", "p", "li", "span"]):
        text = clean_text(elem.get_text(" "))
        if len(text) >= 25:
            chunks.append(text)

    return "\n".join(chunks)


def find_internal_links(base_url: str, html: str, max_links: int = MAX_EXTRA_PAGES) -> list[str]:
    if not html:
        return []

    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc.lower().replace("www.", "")
    soup = BeautifulSoup(html, "html.parser")

    preferred = (
        "about",
        "unternehmen",
        "leistungen",
        "produkte",
        "services",
        "kompetenzen",
        "produktion",
        "fertigung",
    )

    seen: set[str] = set()
    scored_links: list[tuple[int, str]] = []

    for a in soup.find_all("a", href=True):
        href = safe(a.get("href"))
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        absolute = urljoin(base_url, href).split("#")[0].rstrip("/")
        parsed = urlparse(absolute)

        if parsed.scheme not in {"http", "https"}:
            continue

        if parsed.netloc.lower().replace("www.", "") != base_domain:
            continue

        if absolute == base_url.rstrip("/") or absolute in seen:
            continue

        seen.add(absolute)
        lower = absolute.lower()

        score = 1
        if any(token in lower for token in preferred):
            score = 0

        scored_links.append((score, absolute))

    return [url for _, url in sorted(scored_links)[:max_links]]


def scrape_website(url: str | None) -> tuple[str, str, str]:
    url = normalize_url(url)
    if not url:
        return "", "NO_WEBSITE", "No website provided."

    try:
        homepage_html = fetch_html(url)
        all_text_parts = [extract_text_from_html(homepage_html)]

        for link in find_internal_links(url, homepage_html, max_links=MAX_EXTRA_PAGES):
            try:
                html = fetch_html(link)
                page_text = extract_text_from_html(html)
                if page_text:
                    all_text_parts.append(f"\nPage: {link}\n{page_text}")
                time.sleep(0.25)
            except Exception:
                continue

        combined = "\n\n".join(part for part in all_text_parts if part)

        if not combined.strip():
            return "", "NO_TEXT", "No useful website text extracted."

        return combined[:MAX_WEBSITE_CHARS], "OK", ""

    except Exception as exc:
        return "", "SCRAPE_ERROR", str(exc)[:1000]


def build_claude_prompt(company: dict[str, Any], website_text: str) -> str:
    payload = {
        "company_name": company.get("name"),
        "website": company.get("website"),
        "legal_form": company.get("legal_form"),
        "purpose": company.get("purpose"),
        "openregister_wz_codes": company.get("openregister_wz_codes"),
        "northdata_wz_code": company.get("northdata_wz_code"),
    }

    return f"""
You are a business analyst and classification assistant.

Analyze the provided company website text and company context.

Return ONLY valid JSON with exactly these keys:
{{
  "business_segment": "broad industry/category only, 1 to 4 words, e.g. 'Cosmetics', 'Food products', 'Industrial manufacturing', 'Healthcare', 'Software', 'Retail', 'Logistics'",
  "business_model": "specific activity/model only, short phrase, e.g. 'machinery manufacturing and contract manufacturing', 'meat processing and distribution', 'organic cold-pressed juices and juice cleanses', 'specialty coffee roasting and retail'",
  "detailed_business_summary": "business activity summary under 150 words explaining what the company does, products/services, customers/markets if clear"
}}

Rules:
- business_segment and business_model must be separate fields.
- Do NOT combine them using a hyphen.
- Do NOT return values like "Cosmetics - machinery manufacturing".
- business_segment should be the broad category only.
- business_model should describe the specific product/service/activity only.
- Use only information supported by the website text and company context.
- Do not invent facts.
- If the website text is weak, keep business_segment broad and business_model conservative.
- detailed_business_summary must stay under 150 words.
- Return valid JSON only. No markdown. No explanation outside JSON.

Examples:
Input meaning: cosmetics machinery manufacturing and contract manufacturing
Output:
{{
  "business_segment": "Cosmetics",
  "business_model": "machinery manufacturing and contract manufacturing",
  "detailed_business_summary": "..."
}}

Input meaning: food products meat processing and distribution
Output:
{{
  "business_segment": "Food products",
  "business_model": "meat processing and distribution",
  "detailed_business_summary": "..."
}}

Input meaning: beverages organic cold-pressed juices and juice cleanses
Output:
{{
  "business_segment": "Beverages",
  "business_model": "organic cold-pressed juices and juice cleanses",
  "detailed_business_summary": "..."
}}

Company context:
{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}

Website text:
{website_text}
""".strip()


def parse_claude_json_response(text: str) -> dict[str, Any]:
    text = safe(text)

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end >= start:
        text = text[start : end + 1]

    return json.loads(text)


def summarize_with_claude(
    api_key: str,
    model_name: str,
    company: dict[str, Any],
    website_text: str,
) -> tuple[str, str, str, str, str, dict[str, Any]]:
    """
    Returns:
    summary, business_segment, business_model, api_status, notes, raw_data
    """
    if not api_key:
        return "", "", "", "CLAUDE_ERROR", "Claude API key missing.", {}

    if not website_text:
        return "", "", "", "NO_TEXT", "No website text extracted.", {}

    client = Anthropic(api_key=str(api_key).strip())

    request_payload = {
        "model": model_name,
        "max_tokens": 650,
        "messages": [{"role": "user", "content": build_claude_prompt(company, website_text)}],
    }

    if "opus-4-7" not in str(model_name).lower():
        request_payload["temperature"] = 0.2

    response = client.messages.create(**request_payload)

    response_text = "\n".join(
        block.text
        for block in response.content
        if getattr(block, "type", "") == "text"
    ).strip()

    if not response_text:
        return "", "", "", "CLAUDE_ERROR", "Empty Claude response.", {}

    try:
        parsed = parse_claude_json_response(response_text)
    except Exception as exc:
        return (
            response_text[:6000],
            "",
            "",
            "PARSE_WARNING",
            f"Could not parse JSON: {exc}",
            {"raw_response": response_text},
        )

    summary = safe(
        parsed.get("detailed_business_summary")
        or parsed.get("detailed_business_segment")
        or parsed.get("detailed_business_model")
    )
    segment = safe(parsed.get("business_segment"))
    business_model = safe(parsed.get("business_model"))

    notes = ""
    api_status = "success"

    if not segment or not business_model:
        api_status = "PARSE_WARNING"
        missing = []
        if not segment:
            missing.append("business_segment")
        if not business_model:
            missing.append("business_model")
        notes = "Claude JSON missing: " + ", ".join(missing)

    return summary, segment, business_model, api_status, notes, {"parsed": parsed, "raw_response": response_text}


def _fetch_companies(supabase, page_size: int = 1000, hard_cap: int = 50000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0

    while len(rows) < hard_cap:
        end = min(start + page_size - 1, hard_cap - 1)

        res = (
            supabase.table("companies")
            .select(
                "openregister_company_id,"
                "register_id,"
                "name,"
                "legal_form,"
                "purpose,"
                "website,"
                "openregister_wz_codes,"
                "northdata_wz_code"
            )
            .order("created_at", desc=True)
            .range(start, end)
            .execute()
        )

        batch = getattr(res, "data", None) or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return rows


def _existing_model_exists(supabase, register_id: str) -> bool:
    res = (
        supabase.table("company_models")
        .select("id")
        .eq("company_register_id", register_id)
        .eq("model_provider", "claude")
        .limit(1)
        .execute()
    )

    return bool(getattr(res, "data", None) or [])


def _delete_existing_model(supabase, register_id: str) -> None:
    (
        supabase.table("company_models")
        .delete()
        .eq("company_register_id", register_id)
        .eq("model_provider", "claude")
        .execute()
    )


def _upsert_model_row(supabase, row: dict[str, Any]) -> None:
    supabase.table("company_models").upsert(row, on_conflict="company_register_id,model_provider").execute()


def run_claude_business_model_enrichment(
    *,
    supabase,
    claude_api_key: str,
    model_name: str = DEFAULT_CLAUDE_MODEL,
    update_existing: bool = False,
) -> dict[str, Any]:
    companies = _fetch_companies(supabase)

    results: list[dict[str, Any]] = []

    processed = 0
    saved = 0
    skipped = 0
    errors = 0
    no_website = 0

    for company in companies:
        register_id = company.get("register_id") or company.get("openregister_company_id")
        company_id = company.get("openregister_company_id")
        company_name = company.get("name") or company_id
        website = normalize_url(company.get("website"))

        if not register_id:
            continue

        try:
            if _existing_model_exists(supabase, register_id) and not update_existing:
                skipped += 1
                results.append({
                    "company": company_name,
                    "status": "skipped",
                    "reason": "existing model",
                })
                continue

            if update_existing:
                _delete_existing_model(supabase, register_id)

            processed += 1

            if not website:
                no_website += 1

                row = {
                    "company_register_id": register_id,
                    "openregister_company_id": company_id,
                    "company_name": company_name,
                    "website": "",
                    "model_provider": "claude",
                    "model_name": model_name,
                    "business_segment": "",
                    "business_model": "",
                    "summary": "",
                    "api_status": "NO_WEBSITE",
                    "notes": "No website available in company details.",
                    "raw_data": {"company": company},
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }

                _upsert_model_row(supabase, row)
                saved += 1

                results.append({
                    "company": company_name,
                    "status": "NO_WEBSITE",
                })

                continue

            website_text, scrape_status, scrape_notes = scrape_website(website)

            if scrape_status != "OK":
                row = {
                    "company_register_id": register_id,
                    "openregister_company_id": company_id,
                    "company_name": company_name,
                    "website": website,
                    "model_provider": "claude",
                    "model_name": model_name,
                    "business_segment": "",
                    "business_model": "",
                    "summary": "",
                    "api_status": scrape_status,
                    "notes": scrape_notes,
                    "raw_data": {
                        "scrape_status": scrape_status,
                        "scrape_notes": scrape_notes,
                    },
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }

                _upsert_model_row(supabase, row)
                saved += 1

                results.append({
                    "company": company_name,
                    "status": scrape_status,
                    "notes": scrape_notes[:120],
                })

                continue

            summary, segment, business_model, api_status, notes, raw_data = summarize_with_claude(
                api_key=claude_api_key,
                model_name=model_name,
                company=company,
                website_text=website_text,
            )

            row = {
                "company_register_id": register_id,
                "openregister_company_id": company_id,
                "company_name": company_name,
                "website": website,
                "model_provider": "claude",
                "model_name": model_name,
                "business_segment": segment,
                "business_model": business_model,
                "summary": summary,
                "api_status": api_status,
                "notes": notes,
                "raw_data": {
                    **(raw_data or {}),
                    "scraped_text_chars": len(website_text),
                },
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            _upsert_model_row(supabase, row)
            saved += 1

            results.append({
                "company": company_name,
                "status": api_status,
                "business_segment": segment,
                "business_model": business_model,
            })

            log_event(
                supabase,
                company_register_id=register_id,
                openregister_company_id=company_id,
                company_name=company_name,
                module="claude_business_model",
                endpoint="anthropic.messages.create",
                status=api_status,
                message=f"Saved Claude business segment/model: {segment} / {business_model}",
            )

        except Exception as exc:
            errors += 1
            msg = str(exc)[:1000]

            results.append({
                "company": company_name,
                "status": "error",
                "error": msg,
            })

            log_event(
                supabase,
                company_register_id=register_id,
                openregister_company_id=company_id,
                company_name=company_name,
                module="claude_business_model",
                endpoint="business_model_enrichment",
                status="error",
                error_message=msg,
            )

    return {
        "companies_seen": len(companies),
        "processed": processed,
        "saved": saved,
        "skipped": skipped,
        "no_website": no_website,
        "errors": errors,
        "results": results,
    }
