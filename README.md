# Succession Analysis — OpenRegister-first

Fresh Streamlit + Supabase project for German succession target discovery.

## Current step

This version includes:

- Clean project structure
- Supabase client
- OpenRegister SDK client
- Advanced company search UI
- Filter builder
- Search run logging
- Company upsert into Supabase with one company row per `openregister_company_id`

Enrichment, Google Sheets sync, scoring, and Excel export will be added in later steps.

## Setup

1. Run `sql/schema.sql` in your new Supabase project's SQL editor.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`.
3. Fill:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `OpenRegister API key is entered in the app UI`
4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Run:

```bash
streamlit run app.py
```

## Dedupe rule

One OpenRegister company appears once in the `companies` table.

The unique key is:

```text
openregister_company_id
```

Search results are saved using Supabase upsert with:

```text
on_conflict="openregister_company_id"
```
