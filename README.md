# Succession Analysis OpenRegister App

OpenRegister-first Streamlit app:

1. Filter-search German companies using OpenRegister Advanced Company Search.
2. Save matched companies to Supabase with one row per `openregister_company_id`.
3. Enrich selected companies with company info, financials, ownership, and UBOs.
4. Sync Supabase data to Google Sheets.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Main file path for Streamlit Cloud

```text
app.py
```

## Secrets

Use `.streamlit/secrets.toml.example` as a template. OpenRegister API key is pasted in the app UI, not in secrets.
