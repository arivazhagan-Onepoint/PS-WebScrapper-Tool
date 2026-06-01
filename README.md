# PS Tender Tracker

A Python-based platform that scrapes UK government tender opportunities from the Find a Tender Service (FTS) OCDS API, applies intelligent filtering and auto-qualification, and populates a shared Google Sheet with structured tender data for business development tracking.

**Version:** 2.0.0 | **Last Updated:** 2026-06-01 | **Lead Data Engineer:** PS Team

---

## Architecture

v2.0.0 introduces a multi-adapter orchestration layer. A top-level orchestrator dispatches one or more adapters, each responsible for a single data portal. The FTS scraper runs as `adapter1`.

```
orchestrator.py           Top-level runner — reads config.json, dispatches adapters
config.json               Adapter registry (portal, type, frequency, enabled flag)
config.py                 Shared configuration (keywords, date logic, column schema)
adapters/
  adapter1/
    main.py               6-step pipeline for adapter1
    scraper.py            FTS OCDS API client + filtering
    tender_parser.py      OCDS JSON → 25-field dict + qualify_tender()
    sheets_writer.py      Google Sheets append / update / column repair
    google_sheets_auth.py OAuth 2.0 token management
    sc_checker.py         Portal page security clearance scanner
    config.py             Adapter-specific path overrides
```

---

## Quick Start

**1. Install dependencies:**
```bash
pip install -r requirements.txt
```

**2. Set up Google Cloud OAuth:**
- See [SETUP.md](SETUP.md) for detailed instructions
- Download OAuth credentials from Google Cloud Console
- Save to `credentials/credentials.json`

**3. Run all enabled adapters:**
```bash
python orchestrator.py
```

**4. Run a single adapter:**
```bash
python orchestrator.py adapter1
```

---

## Pipeline (per adapter)

Each adapter runs a 6-step pipeline on every execution:

| Step | Action |
|------|--------|
| 1 | Initialise FTS OCDS API scraper |
| 2 | Fetch releases and apply filters (keyword, CPV, country, status) |
| 3 | Parse OCDS JSON into 25-field tender dict |
| 4 | SC Flag check — fetch each portal page and scan for clearance requirements |
| 5 | Qualify tenders (`PreQualified` / `NotQualified`) |
| 6 | Write new rows / update existing rows in Google Sheet |

Raw API extracts are saved to `adapters/adapter1/extract_json/` and parsed outputs to `adapters/adapter1/target_json/` after each run.

---

## Filtering

| Filter | Logic |
|--------|-------|
| **Keywords** | 54 terms across AI/ML, Data, Cloud/Integration, Delivery, Social Value, Sector — matched against title + description |
| **CPV Codes** | 72000000 family (IT services) — matched against tender, items, and lots |
| **Country** | United Kingdom only — matched against buyer party address |
| **Status** | Excludes `complete` (closed/awarded) notices |
| **Publication window** | Rolling 7 days (rounded to Monday when run mid-week) |
| **Due date window** | 2–14 days ahead (rounded forward to Saturday) |

---

## Auto-Qualification

After parsing, each tender is automatically assigned a `Status`:

| Condition | Result |
|-----------|--------|
| Planning stage + annual value < £1,000,000 | `PreQualified` |
| Tender/opportunity stage + annual value < £139,689 | `PreQualified` |
| Annual value not available | `PreQualified` (benefit of the doubt) |
| All other value/stage combinations | `NotQualified` |
| Due date present but outside window | `NotQualified` (overrides above) |

Manual status overrides set directly in the sheet (`Shortlisted`, `Rejected`, etc.) are preserved on subsequent runs and are never overwritten by the qualification logic.

---

## Dataset Fields (25 columns)

| # | Column | Source |
|---|--------|--------|
| A | Portal Name | Static: `Find-A-Tender` |
| B | Adapter | Adapter ID from `config.json` |
| C | Direct URL | `release.id` → portal URL |
| D | Published On | `release.date` |
| E | ID | `release.id` (notice number) |
| F | OCID | `release.ocid` — deduplication key |
| G | Name | `tender.title` |
| H | Due Date | `tenderPeriod.endDate` → milestones → EOI deadline → futureNoticeDate |
| I | Procurement Stage | `release.tag` + `release.initiationType` |
| J | Total Contract Value | `tender.value` → lots → awards → contracts (`amountGross` preferred) |
| K | Contract Duration | `lots[].contractPeriod` → awards → contracts → `tender.contractPeriod` |
| L | Annual Contract Value | Total Value ÷ (base months / 12) |
| M | Tender Description | `tender.description` |
| N | Buyer Name | `buyer.name` → parties lookup |
| O | CPV Code | `tender.classification` + items + lots |
| P | SC_Flag | `True` / `False` / `TBD` — set by SC checker |
| Q | Country | Buyer party `address.countryName` |
| R | Locality | Buyer party `address.locality` |
| S | Suitable for SMEs? | `tender.suitability.sme` + `lots[].suitability.sme` |
| T | Status | Auto-set: `PreQualified` / `NotQualified`; manual values preserved |
| U | Status Date | Auto-stamped when Status changes |
| V | Processed Date | Run timestamp |
| W | Comments | Timestamped audit trail; SC result + change diff appended on each run |
| X | Last Modified Date | Stamped on every update |
| Y | Created Date | First-seen timestamp |

---

## Google Sheets Integration

- **OAuth 2.0** via service credentials (`credentials/credentials.json` + token cache)
- **Auto-creates** the `PS Tender Tracker` sheet in the configured Google Drive folder if absent
- **New tenders** appended as rows; **existing tenders** updated in-place (matched by OCID)
- **Adapter-scoped** — each adapter only manages rows it originally wrote; multiple adapters share the same sheet without collision
- **Column auto-repair** — missing columns are inserted at the correct position on every run without disturbing existing data
- **Rate limit handling** — exponential backoff with jitter, up to 6 retries, capped at 120 s

---

## Security Clearance (SC) Flag

After parsing, each tender's portal page is fetched and scanned for UK government security clearance requirements:

| Level | Matched as |
|-------|-----------|
| Enhanced Developed Vetting (eDV) | Full phrase + `\beDV\b` |
| Enhanced Security Check (eSC) | Full phrase + `\beSC\b` |
| Counter Terrorist Check (CTC) | Full phrase + `\bCTC\b` |
| Developed Vetting (DV) | Full phrase + context-paired `DV` |
| Security Check (SC) | Full phrase + context-paired `SC` |

`SC_Flag` is set to `True` (with found terms), `False` (page loaded, none found), or `TBD` (page unreachable). The result is appended as a timestamped entry in the `Comments` column, and SC_Flag changes are tracked in update diffs on subsequent runs.

---

## Configuration

Edit `config.py` to customise shared settings:

- **Keywords** — add/remove tender categories
- **CPV codes** — extend or narrow the IT services filter
- **Date ranges** — publication window and due date window logic
- **Sheet name and Drive folder ID**

Edit `config.json` to add, disable, or reorder adapters.

---

## Logging

| File | Contents |
|------|----------|
| `adapters/adapter1/adapter1.log` | Full run log for adapter1 (console + file) |

End-of-run summary includes: tenders found, parsed, SC flag totals, written, updated, skipped, errors.

---

## Project Status

**Completed:**
- Multi-adapter orchestration framework
- FTS OCDS API scraper with CPV, keyword, country, and status filters
- 25-field tender parser with 4-priority due date fallback
- Auto-qualification (PreQualified / NotQualified)
- Manual status preservation
- SC clearance detection (5 clearance levels)
- Google Sheets OAuth, append, batch update, column auto-repair
- OCID-based deduplication (adapter-scoped)

**Future Enhancements:**
- Additional adapters (Contracts Finder, Sell2Wales, etc.)
- Automated scheduling (daily/weekly runs)
- Email notifications on new PreQualified tenders
- Historical trend analysis

---

## Troubleshooting

See [SETUP.md](SETUP.md) for full installation and first-run instructions.

**Common issues:**
- `Credentials file not found` — download OAuth credentials from Google Cloud Console and save to `credentials/credentials.json`
- `No tenders found` — check keyword/CPV filters and date ranges in `config.py`
- `Token refresh failed` — delete `credentials/ps_tender_token.json` and re-run to re-authenticate
- `HttpError 403/429` — Sheets API rate limit; the tool retries automatically with backoff
