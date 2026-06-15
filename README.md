# Succession Analysis — OpenRegister

OpenRegister-first Streamlit app for German succession/acquisition target discovery.

## Main workflow

1. Run OpenRegister advanced filter search.
2. Save deduped companies into Supabase.
3. Enrich selected data: company info, financials, direct ownership, UBO/control-chain.
4. Generate Claude business-model summaries from company websites.
5. Generate Claude fit scores using dynamic scoring parameters.
6. Sync clean readable tables to Google Sheets.
7. Export filtered Excel workbooks containing Overview plus related detail sheets.

## Secrets

OpenRegister and Claude keys are pasted in the app UI.

Streamlit secrets should contain only backend credentials:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR-SUPABASE-SERVICE-ROLE-KEY"
GOOGLE_SHEET_ID = "YOUR-GOOGLE-SHEET-ID"
GOOGLE_SERVICE_ACCOUNT_JSON = '''{...}'''
```

## Deploy

Main file path:

```text
app.py
```

## Required SQL

Run `sql/schema.sql` for a clean Supabase project.

If you already deployed v0.7, run `sql/migration_v0_8_fix_sheet_formats.sql`. This refreshes `master_overview` to remove Recommended Action from the overview output. v0.8 also fixes Google Sheets old-format carryover that caused percentage/count columns to display as 1900 dates.
