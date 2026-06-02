import logging
import time
import random
from datetime import datetime
from googleapiclient.errors import HttpError
from .config import TARGET_FOLDER_ID, SHEET_NAME, DATASET_FIELDS, ADAPTER_ID
from .google_sheets_auth import get_authenticated_service

logger = logging.getLogger(__name__)

RATE_LIMIT_STATUS_CODES = (429, 403)


def _col_letter(n):
    """Convert 1-based column number to spreadsheet column letter (1=A, 26=Z, 27=AA…)."""
    result = ''
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


LAST_COL = _col_letter(len(DATASET_FIELDS))

# Fields compared between old and new to detect meaningful changes.
# Tuple format: (dataset field name, short label for comment, max chars or None)
CHANGE_FIELDS = [
    ('Tender Status',         'Status',       None),
    ('Total Contract Value',  'Value',        None),
    ('Contract Duration',     'Duration',     None),
    ('Due Date',              'Due',          None),
    ('Procurement Stage',     'Stage',        50),
    ('Buyer Name',            'Buyer',        50),
    ('Annual Contract Value', 'Annual',       None),
    ('SC_Flag',               'SC_Flag',      None),
    ('Name',                  'Name',         60),
    ('Published On',          'Published',    None),
    ('CPV Code',              'CPV',          60),
    ('Suitable for SMEs?',    'SME',          None),
    ('Country',               'Country',      None),
    ('Locality',              'Locality',     None),
]


def dedup_by_ocid(tenders):
    """For tenders sharing the same OCID, keep only the one with the latest Published On date."""
    ocid_index = {}
    result = []
    for tender in tenders:
        ocid = tender.get('OCID', '')
        if not ocid:
            result.append(tender)
            continue
        if ocid not in ocid_index:
            ocid_index[ocid] = len(result)
            result.append(tender)
        else:
            pos = ocid_index[ocid]
            kept_date = result[pos].get('Published On', '')
            this_date = tender.get('Published On', '')
            if this_date > kept_date:
                logger.info(f"OCID {ocid}: replacing {kept_date} with newer release {this_date}")
                result[pos] = tender
            else:
                logger.info(f"OCID {ocid}: skipping older release {this_date} (keeping {kept_date})")
    return result


class SheetsWriter:
    def __init__(self, adapter_id=ADAPTER_ID):
        self.sheets_service = get_authenticated_service('sheets', 'v4')
        self.drive_service = get_authenticated_service('drive', 'v3')
        self.adapter_id = adapter_id
        self.sheet_id = None
        self.sheet_tab_id = None     # numeric tab ID for batchUpdate requests
        self.existing_ocid_rows = {} # ocid -> sheet row number (scoped to this adapter)
        self.existing_row_data = {}  # row  -> {field: value} for all columns

    def _execute_with_retry(self, request_fn, max_retries=6):
        """Execute a Sheets API call, retrying on rate limit errors with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return request_fn()
            except HttpError as e:
                if e.resp.status in RATE_LIMIT_STATUS_CODES and attempt < max_retries - 1:
                    wait = min(120, (2 ** attempt) * 5 + random.uniform(0, 2))
                    logger.warning(f"Rate limit hit (attempt {attempt + 1}/{max_retries}), retrying in {wait:.1f}s...")
                    time.sleep(wait)
                else:
                    raise

    def get_or_create_sheet(self):
        """Get existing sheet or create new one in target folder."""
        try:
            self.sheet_id = self.find_sheet_in_folder()

            if self.sheet_id:
                logger.info(f"Found existing sheet: {self.sheet_id}")
                self._get_sheet_tab_id()
                self._ensure_columns_match()
                self._freeze_header_row()
                self.load_existing_records()
                return self.sheet_id

            logger.info("Creating new sheet...")
            spreadsheet_body = {
                'properties': {'title': SHEET_NAME},
                'sheets': [{
                    'properties': {
                        'title': SHEET_NAME,
                        'gridProperties': {
                            'rowCount': 1000,
                            'columnCount': len(DATASET_FIELDS)
                        }
                    }
                }]
            }

            spreadsheet = self._execute_with_retry(
                lambda: self.sheets_service.spreadsheets().create(
                    body=spreadsheet_body,
                    fields='spreadsheetId,sheets.properties.sheetId'
                ).execute()
            )

            self.sheet_id = spreadsheet['spreadsheetId']
            self.sheet_tab_id = spreadsheet.get('sheets', [{}])[0].get('properties', {}).get('sheetId', 0)
            logger.info(f"Created new sheet: {self.sheet_id}")

            self.move_sheet_to_folder(self.sheet_id)
            self.add_headers()
            self._freeze_header_row()

            return self.sheet_id

        except HttpError as e:
            logger.error(f"Error managing sheet: {e}")
            raise

    def find_sheet_in_folder(self):
        """Find sheet by name in target folder."""
        try:
            query = (
                f"name='{SHEET_NAME}' and '{TARGET_FOLDER_ID}' in parents "
                f"and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
            )
            results = self._execute_with_retry(
                lambda: self.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name)',
                    pageSize=1,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()
            )

            files = results.get('files', [])
            return files[0]['id'] if files else None

        except HttpError as e:
            logger.error(f"Error finding sheet: {e}")
            return None

    def _get_sheet_tab_id(self):
        """Fetch the numeric tab ID (sheetId) for the named sheet tab — needed for batchUpdate requests."""
        spreadsheet = self._execute_with_retry(
            lambda: self.sheets_service.spreadsheets().get(
                spreadsheetId=self.sheet_id,
                fields='sheets.properties'
            ).execute()
        )
        for sheet in spreadsheet.get('sheets', []):
            props = sheet.get('properties', {})
            if props.get('title') == SHEET_NAME:
                self.sheet_tab_id = props['sheetId']
                return
        self.sheet_tab_id = 0

    def _ensure_columns_match(self):
        """Insert any columns present in DATASET_FIELDS but missing from the sheet's header row."""
        result = self._execute_with_retry(
            lambda: self.sheets_service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=f"'{SHEET_NAME}'!1:1"
            ).execute()
        )
        sheet_headers = result.get('values', [[]])[0] if result.get('values') else []

        requests = []
        for expected_idx, field in enumerate(DATASET_FIELDS):
            if field in sheet_headers:
                continue
            # Column is missing — insert a blank column at the expected position
            insert_at = expected_idx
            requests.append({
                "insertDimension": {
                    "range": {
                        "sheetId": self.sheet_tab_id,
                        "dimension": "COLUMNS",
                        "startIndex": insert_at,
                        "endIndex": insert_at + 1
                    },
                    "inheritFromBefore": False
                }
            })
            requests.append({
                "updateCells": {
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": field}}]}],
                    "fields": "userEnteredValue",
                    "start": {
                        "sheetId": self.sheet_tab_id,
                        "rowIndex": 0,
                        "columnIndex": insert_at
                    }
                }
            })
            # Keep local list in sync so subsequent field positions are calculated correctly
            sheet_headers.insert(insert_at, field)
            logger.info(f"Queued column insertion: '{field}' at position {insert_at + 1}")

        if requests:
            self._execute_with_retry(
                lambda: self.sheets_service.spreadsheets().batchUpdate(
                    spreadsheetId=self.sheet_id,
                    body={"requests": requests}
                ).execute()
            )
            logger.info("Sheet columns updated to match DATASET_FIELDS")

    def move_sheet_to_folder(self, sheet_id):
        """Move sheet to target folder."""
        try:
            self._execute_with_retry(
                lambda: self.drive_service.files().update(
                    fileId=sheet_id,
                    addParents=TARGET_FOLDER_ID,
                    fields='id, parents',
                    supportsAllDrives=True
                ).execute()
            )
            logger.info(f"Moved sheet to folder: {TARGET_FOLDER_ID}")

        except HttpError as e:
            logger.warning(f"Error moving sheet: {e}")

    def add_headers(self):
        """Add header row to sheet."""
        try:
            self._execute_with_retry(
                lambda: self.sheets_service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"'{SHEET_NAME}'!A1:{LAST_COL}1",
                    valueInputOption='RAW',
                    body={'values': [DATASET_FIELDS]}
                ).execute()
            )
            logger.info("Headers added to sheet")

        except HttpError as e:
            logger.error(f"Error adding headers: {e}")

    def _freeze_header_row(self):
        """Freeze the first row so the header stays visible when scrolling."""
        try:
            self._execute_with_retry(
                lambda: self.sheets_service.spreadsheets().batchUpdate(
                    spreadsheetId=self.sheet_id,
                    body={"requests": [{
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": self.sheet_tab_id,
                                "gridProperties": {"frozenRowCount": 2}
                            },
                            "fields": "gridProperties.frozenRowCount"
                        }
                    }]}
                ).execute()
            )
            logger.info("Header row frozen")
        except HttpError as e:
            logger.warning(f"Error freezing header row: {e}")

    def load_existing_records(self):
        """Load all columns for existing rows, mapping URL/ID/OCID to row numbers and storing full row data."""
        try:
            result = self._execute_with_retry(
                lambda: self.sheets_service.spreadsheets().values().get(
                    spreadsheetId=self.sheet_id,
                    range=f"'{SHEET_NAME}'!A:{LAST_COL}"
                ).execute()
            )

            values = result.get('values', [])
            if not values:
                return

            # Build column index from actual sheet headers (row 1) so existing data
            # is read correctly even if column positions differ from DATASET_FIELDS.
            actual_headers = values[0]
            actual_field_idx = {h: i for i, h in enumerate(actual_headers) if h}

            for idx, row in enumerate(values[1:], start=2):
                row_data = {}
                for field in DATASET_FIELDS:
                    col_i = actual_field_idx.get(field)
                    row_data[field] = (row[col_i] if col_i is not None and col_i < len(row) else '')

                ocid        = row_data.get('OCID', '')
                row_adapter = row_data.get('Adapter', '')

                # Only index rows that belong to this adapter so each adapter
                # manages its own records exclusively and never overwrites another's.
                if ocid and row_adapter.lower() == self.adapter_id.lower():
                    self.existing_ocid_rows[ocid] = idx
                self.existing_row_data[idx] = row_data

            logger.info(
                f"Loaded {len(self.existing_ocid_rows)} existing OCIDs "
                f"for adapter '{self.adapter_id}'"
            )

        except HttpError as e:
            logger.warning(f"Error loading existing records: {e}")

    def _build_update_comment(self, tender, existing_data, ts, status_reason=''):
        """Generate a change-diff comment or 'no changes' note for an updated record.
        When status_reason is provided it is appended inline to the Status change entry."""
        changes = []
        for field, label, max_len in CHANGE_FIELDS:
            old = str(existing_data.get(field, '')).strip()
            new = str(tender.get(field, '')).strip()
            if new and old != new:
                if max_len:
                    old = (old[:max_len] + '…') if len(old) > max_len else old
                    new = (new[:max_len] + '…') if len(new) > max_len else new
                entry = f"{label}: {old or '-'} -> {new}"
                if field == 'Tender Status' and status_reason:
                    entry += f" ({status_reason})"
                changes.append(entry)
        if changes:
            return f"[{ts}] Updated | " + " | ".join(changes)
        return f"[{ts}] Re-scraped, no changes"

    def find_existing_row(self, ocid):
        """Return sheet row number if this tender already exists (matched by OCID), else None."""
        if ocid and ocid in self.existing_ocid_rows:
            return self.existing_ocid_rows[ocid]
        return None


    def write_batch(self, tenders):
        """Append new tenders and update existing ones in the sheet."""
        results = {'written': 0, 'updated': 0, 'skipped_in_batch': 0, 'errors': 0}

        from .config import UK_TIMEZONE
        now = datetime.now(UK_TIMEZONE).isoformat()

        # Dedup is already applied in main.py before SC checking; this is a safety net.
        tenders = dedup_by_ocid(tenders)

        new_rows = []
        updates = []       # list of (row_number, row_values) for existing records
        seen_in_batch = {} # url/id -> first-seen tender, to dedup within the batch

        for tender in tenders:
            try:
                tender_id  = tender.get('ID', '') or 'Unknown'
                tender['ID'] = tender_id
                tender_ocid = tender.get('OCID', '')

                # Dedup within batch by OCID only
                if tender_ocid and tender_ocid in seen_in_batch:
                    logger.info(f"Skipping within-batch duplicate OCID: {tender_ocid} | ID: {tender_id}")
                    results['skipped_in_batch'] += 1
                    continue
                if tender_ocid:
                    seen_in_batch[tender_ocid] = True

                existing_row = self.find_existing_row(tender_ocid)
                qualify_comment = tender.pop('_qualify_comment', '')

                today = datetime.fromisoformat(now).strftime('%Y-%m-%d')

                # System-generated qualification values — anything else is a manual override
                SYSTEM_STATUSES = {'PreQualified', 'NotQualified', ''}

                if existing_row:
                    ts = datetime.fromisoformat(now).strftime('%Y-%m-%d %H:%M')
                    existing_data = self.existing_row_data.get(existing_row, {})
                    # Preserve Created Date, stamp Last Modified Date
                    tender['Last Modified Date'] = now
                    tender['Created Date'] = existing_data.get('Created Date') or now
                    # If the sheet holds a manually set status (not a system value),
                    # restore it and leave Status Date untouched — do not overwrite.
                    old_status = str(existing_data.get('Tender Status', '')).strip()
                    if old_status not in SYSTEM_STATUSES:
                        tender['Tender Status'] = old_status
                        tender['Tender Status Date'] = existing_data.get('Tender Status Date', '')
                        logger.info(f"Preserving manual status '{old_status}' for OCID {tender_ocid} - qualification not applied")
                    else:
                        # System status — apply qualification and stamp Status Date on change
                        new_status = str(tender.get('Tender Status', '')).strip()
                        if new_status and new_status != old_status:
                            tender['Tender Status Date'] = today
                            logger.info(f"Status changed for OCID {tender_ocid}: '{old_status}' -> '{new_status}' | Status Date set to {today}")
                        else:
                            tender['Tender Status Date'] = existing_data.get('Tender Status Date', '')
                    # Build comments: existing sheet comments + change diff (reason inlined on status change)
                    status_reason = qualify_comment.split(' | ', 1)[-1] if qualify_comment else ''
                    diff = self._build_update_comment(tender, existing_data, ts, status_reason)
                    prior = existing_data.get('Comments', '')
                    tender['Comments'] = (prior + '\n' + diff) if prior else diff
                    row_values = [tender.get(field, '') for field in DATASET_FIELDS]
                    updates.append((existing_row, row_values))
                else:
                    # New record: append qualify comment to first-scraped + SC check comments
                    prior = tender.get('Comments', '')
                    tender['Comments'] = (prior + '\n' + qualify_comment) if prior else qualify_comment
                    # Stamp Tender Status Date, Created Date, and Last Modified Date
                    tender['Tender Status Date'] = today
                    tender['Last Modified Date'] = now
                    tender['Created Date'] = now
                    row_values = [tender.get(field, '') for field in DATASET_FIELDS]
                    new_rows.append(row_values)

            except Exception as e:
                logger.error(f"Error preparing tender: {e}")
                results['errors'] += 1

        # Append new records in one API call
        if new_rows:
            try:
                self._execute_with_retry(
                    lambda: self.sheets_service.spreadsheets().values().append(
                        spreadsheetId=self.sheet_id,
                        range=f"'{SHEET_NAME}'!A:{LAST_COL}",
                        valueInputOption='RAW',
                        insertDataOption='INSERT_ROWS',
                        body={'values': new_rows}
                    ).execute()
                )
                results['written'] = len(new_rows)
                logger.info(f"Appended {len(new_rows)} new tenders")
            except HttpError as e:
                logger.error(f"Error appending new tenders: {e}")
                results['errors'] += len(new_rows)

        # Update existing records in one batchUpdate call
        if updates:
            try:
                update_body = {
                    'valueInputOption': 'RAW',
                    'data': [
                        {
                            'range': f"'{SHEET_NAME}'!A{row_num}:{LAST_COL}{row_num}",
                            'values': [row_values]
                        }
                        for row_num, row_values in updates
                    ]
                }
                self._execute_with_retry(
                    lambda: self.sheets_service.spreadsheets().values().batchUpdate(
                        spreadsheetId=self.sheet_id,
                        body=update_body
                    ).execute()
                )
                results['updated'] = len(updates)
                logger.info(f"Updated {len(updates)} existing tenders")
            except HttpError as e:
                logger.error(f"Error updating existing tenders: {e}")
                results['errors'] += len(updates)

        logger.info(f"Batch write results: {results}")
        return results
