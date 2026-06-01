# PS Tender Tracker — Setup Guide

## Prerequisites

- Python 3.14+
- Google account with access to Google Drive and Google Sheets
- Google Cloud project

---

## Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 2: Set Up Google Cloud OAuth

### 2a. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project — e.g. `PS Tender Tracker`
3. Enable the following APIs:
   - Google Sheets API
   - Google Drive API

### 2b. Create OAuth 2.0 Credentials

1. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**
2. Choose **Desktop application**
3. Download the JSON file and save it as `credentials/credentials.json`

Your `credentials/` directory should look like this:

```
credentials/
├── credentials.json        # OAuth credentials (you provide this)
├── ps_tender_token.json    # Generated automatically on first run
└── .gitignore              # Prevents credentials from being committed
```

> **Important:** `credentials.json` and `ps_tender_token.json` are excluded from git by `.gitignore`. Never commit them.

### 2c. First-Run Authentication

The first time you run the tool it will:

1. Open a browser window asking you to authorise access to Google Sheets and Drive
2. Store the token in `credentials/ps_tender_token.json` for future runs
3. Automatically refresh the token on subsequent runs

---

## Step 3: Configuration

### Shared configuration — `config.py`

This file controls settings that apply to all adapters:

| Setting | Description |
|---------|-------------|
| `TARGET_FOLDER_ID` | Google Drive folder ID where the sheet lives |
| `SHEET_NAME` | Name of the target Google Sheet |
| `KEYWORDS` | List of 54 terms used to filter tenders |
| `CPV_CODES` | IT services CPV code prefixes |
| `INCLUDED_COUNTRIES` | Country filter (default: `United Kingdom`) |
| `EXCLUDED_STATUSES` | Tender statuses to skip (default: `complete`) |
| `get_publication_date_range()` | Rolling 7-day publication window logic |
| `get_due_date_range()` | 2–14 day due date window logic |

### Adapter registry — `config.json`

Controls which adapters are active:

```json
{
  "adapters": [
    {
      "s_no": 1,
      "portal": "Find a Tender Service",
      "url": "https://www.find-tender.service.gov.uk",
      "frequency": "daily",
      "type": "OCDS API",
      "enabled": true,
      "adapter_id": "Adapter1",
      "module": "adapters.adapter1.main"
    }
  ]
}
```

Set `"enabled": false` to disable an adapter without removing it. Add new entries to register additional adapters.

---

## Step 4: Run the Tool

### Run all enabled adapters

```bash
python orchestrator.py
```

### Run a single adapter

```bash
python orchestrator.py adapter1
```

### On first run

1. A browser window opens asking for Google authorisation
2. Grant permission to access Google Sheets and Drive
3. The token is saved to `credentials/ps_tender_token.json` automatically
4. Scraping begins immediately

### What happens on each run

1. FTS OCDS API is queried for releases in the configured date window
2. Releases are filtered by keyword, CPV code, country, and status
3. Matching tenders are parsed into 25-field records
4. Each tender's portal page is fetched and scanned for security clearance requirements
5. Tenders are auto-qualified as `PreQualified` or `NotQualified`
6. New tenders are appended to the Google Sheet; existing tenders are updated in-place

### Output locations

| Output | Location |
|--------|----------|
| Google Sheet | `PS Tender Tracker` in your configured Drive folder |
| Raw API extracts | `adapters/adapter1/extract_json/extract_YYYYMMDD_HHMMSS.json` |
| Parsed tender data | `adapters/adapter1/target_json/tenders_YYYYMMDD_HHMMSS.json` |
| Run log | `adapters/adapter1/adapter1.log` |

---

## File Structure

```
PS WebScrapper Tool/
├── orchestrator.py              # Top-level runner — dispatches adapters
├── config.json                  # Adapter registry
├── config.py                    # Shared configuration and column schema
├── requirements.txt
├── README.md
├── SETUP.md                     # This file
├── RELEASE_NOTES.md
├── credentials/
│   ├── credentials.json         # OAuth credentials (you provide — not in git)
│   ├── ps_tender_token.json     # Auth token (auto-generated — not in git)
│   └── .gitignore
└── adapters/
    └── adapter1/
        ├── main.py              # 6-step pipeline
        ├── scraper.py           # FTS OCDS API client and filtering
        ├── tender_parser.py     # OCDS JSON → 25-field dict + qualify_tender()
        ├── sheets_writer.py     # Google Sheets append / update / column repair
        ├── google_sheets_auth.py
        ├── sc_checker.py        # Security clearance page scanner
        ├── config.py            # Adapter-specific path overrides
        ├── extract_json/        # Raw API responses (not in git)
        └── target_json/         # Parsed tender JSON outputs (not in git)
```

---

## Troubleshooting

### "Credentials file not found"
Download `credentials.json` from Google Cloud Console (Credentials → OAuth 2.0 Client IDs → Download) and save it to `credentials/credentials.json`.

### "Token refresh failed"
Delete `credentials/ps_tender_token.json` and re-run. A browser window will open for re-authentication.

### No tenders found
- Check keyword and CPV filters in `config.py`
- Confirm date range logic in `get_publication_date_range()` and `get_due_date_range()`
- Verify the FTS API is reachable: `https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages`

### HttpError 403 or 429 (Google Sheets)
- Confirm both Sheets API and Drive API are enabled in your Google Cloud project
- Check that your OAuth credentials have the correct scopes (`spreadsheets` + `drive`)
- The tool retries automatically with exponential backoff — if errors persist, check your API quota in Google Cloud Console

### Sheet columns out of order or missing
The tool auto-repairs the sheet header on every run via `_ensure_columns_match()`. If the sheet has manually added columns that conflict, rename or remove them before the next run.

### "Adapter not found" / module import error
Confirm the `module` value in `config.json` matches the actual Python import path (e.g. `adapters.adapter1.main`) and that `adapters/__init__.py` and `adapters/adapter1/__init__.py` both exist.

---

## Notes

- All dates use UK timezone (`Europe/London`)
- Publication window start is rounded back to the previous Monday when run mid-week
- Due date window end is rounded forward to the next Saturday
- Deduplication is OCID-based and adapter-scoped — each adapter manages only its own rows
- Manual status values set in the sheet (`Shortlisted`, `Rejected`, etc.) are never overwritten by auto-qualification
