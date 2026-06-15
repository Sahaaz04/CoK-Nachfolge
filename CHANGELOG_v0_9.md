# v0.9 quota fix

## Problem

The v0.8 Google Sheets formatting fix was correct in intention but too chatty: it used multiple separate Google Sheets write requests per sheet and per numeric column. Google rejected the sync with a `429 quota exceeded` error for write requests per minute.

## Fix

- Consolidated header styling, old-format clearing, frozen row, basic filter, and numeric column formats into one `spreadsheets.batchUpdate` request per sheet.
- Removed per-column `worksheet.format(...)` calls.
- Kept the 1900-date fix: old cell formats are still cleared and numeric columns are still forced back to number format.
- No SQL migration required for v0.9 if v0.8 migration has already been run.

## Files changed

- `modules/google_sheets_sync.py`
- `README.md`
- `CHANGELOG_v0_9.md`
