# v0.8 audit and fixes

## Root causes fixed

1. **1900 / 1899 dates appearing in percentage/count columns**
   - Cause: Google Sheets keeps old column formatting after `worksheet.clear()`.
   - If a reused column was previously formatted as a date, numeric values like `100`, `95`, or `61` displayed as 1900-era dates.
   - Fix: Google Sheets sync now clears both values and previous cell formatting, then explicitly formats numeric columns as numbers.

2. **Legacy duplicate UBO sheet**
   - Cause: the old sheet tab `UBOs` could remain in the user's existing Google Sheet after the app switched to `UBO Control Chain`.
   - Fix: sync now deletes the legacy `UBOs` tab and writes only `UBO Control Chain`.

3. **Recommended Action column**
   - Fix: removed from `master_overview` and hidden from Google Sheets / filtered Excel export display. It still remains stored in the DB fit-score table if needed later.

4. **Duplicate / technical ID display**
   - Fix: `register_id`, `company_register_id`, and `lei` remain in Supabase for backend logic but are hidden from Sheets and Excel exports.

## Files changed

- `modules/google_sheets_sync.py`
  - Clears old values and formatting before each write.
  - Applies numeric formats to count, age, money, and percentage columns.
  - Deletes legacy `UBOs` sheet.
  - Hides `recommended_action` from sheets.

- `modules/filtered_workbook_export.py`
  - Uses the same cleaned display-column rules as Google Sheets.
  - Keeps filtered workbook export multi-sheet, not overview-only.

- `sql/schema.sql`
  - `master_overview` no longer exposes `recommended_action`.

- `sql/migration_v0_8_fix_sheet_formats.sql`
  - Recreates `master_overview` with the corrected column set.

- `README.md`
  - Updated required SQL migration note.

## What to do after replacing files

1. Run `sql/migration_v0_8_fix_sheet_formats.sql` in Supabase.
2. Redeploy/restart the Streamlit app.
3. Run Google Sheets sync once.
4. The bad 1900-date formatting should disappear because the sync clears old formats before writing new values.
