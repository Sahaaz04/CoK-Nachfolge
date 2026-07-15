# CoK-Nachfolge — Project Context

## What this is
A Streamlit tool for German company succession prospecting

## Architecture

**Live app:** `app.py` + everything in `modules/` — this is the real,
deployed code. Streamlit Cloud runs it directly from this repo in Github, connected
live to Supabase (Postgres) as the backend.

**`sql/` folder — documentation only, not live:**
- `schema.sql`, `query.two`, `querythree`, `queryfour`, `queryfive` — these
  reflect the Supabase schema/queries but are run manually in the Supabase
  SQL editor. Editing these files does NOT change the live database.
- `appscript` — reflects the Google Apps Script used inside the connected
  Sheet, but is run from the Sheets extension, not from this repo. Editing
  it here does NOT change the live script.

## Working rules
- Only do what I explicitly ask. Do not refactor, "improve," rename, or
  restructure anything I didn't ask about — even if it looks like a good
  idea. If you notice something worth changing, mention it and ask, don't
  just do it.
- When done, summarize exactly what changed and which files.
