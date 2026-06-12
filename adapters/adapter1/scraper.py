import json
import logging
import os
import re
import time
import requests
from .config import (
    FTS_API_BASE, FTS_API_KEY, PORTAL_URL, PORTAL_NAME,
    CPV_CODES, SUITABLE_FOR_SMES, EXCLUDED_STATUSES, EXCLUDED_TAGS, INCLUDED_COUNTRIES, KEYWORDS,
    get_publication_date_range, get_due_date_range, UK_TIMEZONE, BASE_DIR
)
from datetime import datetime

logger = logging.getLogger(__name__)


class TenderScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if FTS_API_KEY:
            self.session.headers['CDP-Api-Key'] = FTS_API_KEY

    def _get(self, url, params=None, max_retries=4):
        """GET with retry on rate limit."""
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=30)
                if response.status_code == 429:
                    wait = int(response.headers.get('Retry-After', 10 * (attempt + 1)))
                    logger.warning(f"Rate limited - waiting {wait}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as e:
                    raw = response.text
                    # Always save raw response for future troubleshooting
                    debug_dir = os.path.join(BASE_DIR, 'extract_json')
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_path = os.path.join(debug_dir, f"malformed_{self._scrape_ts}.txt")
                    with open(debug_path, 'w', encoding='utf-8') as f:
                        f.write(raw)
                    logger.warning(f"Malformed JSON saved to: {debug_path}")
                    # Attempt targeted repair: leading zeros on decimal numbers (e.g. 00.00 → 0.00)
                    repaired = re.sub(r'(:\s*)0{2,}(\.\d+)', r'\g<1>0\2', raw)
                    if repaired != raw:
                        try:
                            data = json.loads(repaired)
                            logger.warning(f"JSON repaired (leading zeros on decimals) for: {url}")
                            return data
                        except ValueError:
                            pass  # repair didn't fully fix it — fall through to diagnostics
                    m = re.search(r'char (\d+)', str(e))
                    if m:
                        pos = int(m.group(1))
                        snippet = raw[max(0, pos - 200): pos + 200]
                        logger.error(f"Malformed JSON from API (char {pos}): {e}")
                        logger.error(f"Context around error (char {pos}):\n{snippet}")
                    else:
                        logger.error(f"Malformed JSON from API: {e}")
                    return None
            except requests.RequestException as e:
                logger.error(f"API request failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
        return None

    def fetch_releases(self, date_from, date_to):
        """Fetch all OCDS releases for the given date range using cursor pagination."""
        releases = []
        params = {
            'updatedFrom': f"{date_from}T00:00:00Z",
            'updatedTo':   f"{date_to}T23:59:59Z",
            'limit':       100,
        }
        url = f"{FTS_API_BASE}/ocdsReleasePackages"
        page = 1

        while url:
            logger.info(f"Fetching API page {page}: {url}")
            data = self._get(url, params=params)
            if not data:
                break

            batch = data.get('releases', [])
            releases.extend(batch)
            logger.info(f"  Page {page}: {len(batch)} releases (total so far: {len(releases)})")

            # Cursor-based pagination — next page URL is in the links
            next_url = data.get('links', {}).get('next')
            url = next_url
            params = None  # params are already encoded in next_url
            page += 1

        logger.info(f"Total releases fetched from API: {len(releases)}")
        return releases

    def matches_keyword(self, release):
        """Return True if any keyword appears in the title or description."""
        tender = release.get('tender', {})
        text = ' '.join(filter(None, [
            tender.get('title', ''),
            tender.get('description', ''),
        ])).lower()
        return any(kw.lower() in text for kw in KEYWORDS)

    def matches_cpv(self, release):
        """Return True if any CPV code in the release matches any of the configured CPV_CODES prefixes."""
        prefixes = [c.rstrip('0') for c in CPV_CODES]
        tender = release.get('tender', {})

        def _check(cl):
            if cl.get('scheme') == 'CPV':
                code = str(cl.get('id', ''))
                return any(code.startswith(p) for p in prefixes)
            return False

        if _check(tender.get('classification', {})):
            return True
        for item in tender.get('items', []):
            if _check(item.get('classification', {})):
                return True
            for ac in item.get('additionalClassifications', []):
                if _check(ac):
                    return True
        for lot in tender.get('lots', []):
            for item in lot.get('items', []):
                if _check(item.get('classification', {})):
                    return True
                for ac in item.get('additionalClassifications', []):
                    if _check(ac):
                        return True
        return False

    def matches_sme(self, release):
        """Return True if the tender is marked suitable for SMEs."""
        if not SUITABLE_FOR_SMES:
            return True  # no filter applied
        suitability = release.get('tender', {}).get('suitability', {})
        return suitability.get('sme', False) is True

    def matches_country(self, release):
        """Return True if the buyer's countryName matches any entry in INCLUDED_COUNTRIES."""
        if not INCLUDED_COUNTRIES:
            return True  # no filter applied
        buyer_id = release.get('buyer', {}).get('id', '')
        for party in release.get('parties', []):
            if party.get('id') == buyer_id or 'buyer' in party.get('roles', []):
                country = party.get('address', {}).get('countryName', '')
                return country in INCLUDED_COUNTRIES
        return False

    def matches_due_date(self, release, due_start):
        """Return True if due date is on or after due_start (lower bound only)."""
        end_date_str = release.get('tender', {}).get('tenderPeriod', {}).get('endDate', '')
        if not end_date_str:
            return True  # no date available — include it
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).date()
            return end_date >= due_start
        except Exception:
            return True

    def build_direct_url(self, release):
        """Construct the notice URL from the release id (e.g. 045337-2026)."""
        notice_id = release.get('id', '')
        return f"{PORTAL_URL}/{notice_id}"

    def scrape(self, run_ts):
        """Fetch and filter tenders from the FTS OCDS API."""
        self._scrape_ts = datetime.fromisoformat(run_ts).strftime('%Y%m%d_%H%M%S')
        pub_start, pub_end = get_publication_date_range()
        due_start = get_due_date_range()

        logger.info("Search parameters:")
        logger.info(f"  Publication Date From : {pub_start}")
        logger.info(f"  Publication Date To   : {pub_end}")
        logger.info(f"  Due Date From         : {due_start}")
        logger.info(f"  CPV Codes             : {', '.join(CPV_CODES)}")
        logger.info(f"  Suitable for SMEs     : {SUITABLE_FOR_SMES}")
        logger.info(f"  Excluded Statuses     : {', '.join(EXCLUDED_STATUSES)}")
        logger.info(f"  Excluded Tags         : {', '.join(sorted(EXCLUDED_TAGS))}")
        logger.info(f"  Included Countries    : {', '.join(INCLUDED_COUNTRIES) if INCLUDED_COUNTRIES else 'All'}")
        logger.info(f"  Keywords ({len(KEYWORDS)})         : {', '.join(KEYWORDS)}")

        all_releases = self.fetch_releases(pub_start, pub_end)

        # Write raw API extract to extract_json folder
        extract_dir = os.path.join(BASE_DIR, 'extract_json')
        os.makedirs(extract_dir, exist_ok=True)
        extract_path = os.path.join(extract_dir, f"extract_{self._scrape_ts}.json")
        with open(extract_path, 'w', encoding='utf-8') as f:
            json.dump(all_releases, f, indent=2, ensure_ascii=False)
        logger.info(f"Raw API extract written to: {extract_path}")

        matched = []
        seen_urls = set()
        filter_counts = {'duplicate': 0, 'keyword': 0, 'cpv': 0, 'sme': 0, 'due_date': 0, 'status_complete': 0, 'country': 0}

        logger.info(f"--- Filtering {len(all_releases)} releases ---")

        for release in all_releases:
            direct_url = self.build_direct_url(release)
            title = release.get('tender', {}).get('title', 'No title')[:60]
            notice_id = release.get('id', '')

            if not self.matches_country(release):
                filter_counts['country'] += 1
                logger.debug(f"  [SKIP-COUNTRY]    {notice_id} | {title}")
                continue

            if release.get('tender', {}).get('status') in EXCLUDED_STATUSES:
                filter_counts['status_complete'] += 1
                logger.debug(f"  [SKIP-STATUS]     {notice_id} | {title}")
                continue

            if EXCLUDED_TAGS.intersection(release.get('tag', [])):
                filter_counts['status_complete'] += 1
                logger.debug(f"  [SKIP-TAG]        {notice_id} | {title} | tags={release.get('tag', [])}")
                continue

            if not self.matches_cpv(release):
                filter_counts['cpv'] += 1
                logger.debug(f"  [SKIP-CPV]        {notice_id} | {title}")
                continue

            if not self.matches_keyword(release):
                filter_counts['keyword'] += 1
                logger.debug(f"  [SKIP-KEYWORD]    {notice_id} | {title}")
                continue

            if direct_url in seen_urls:
                filter_counts['duplicate'] += 1
                logger.debug(f"  [SKIP-DUPLICATE]  {notice_id} | {title}")
                continue
            seen_urls.add(direct_url)

            # if not self.matches_sme(release):
            #     filter_counts['sme'] += 1
            #     logger.debug(f"  [SKIP-SME]        {notice_id} | {title}")
            #     continue

            # if not self.matches_due_date(release, due_start):
            #     filter_counts['due_date'] += 1
            #     logger.debug(f"  [SKIP-DUE-DATE]   {notice_id} | {title}")
            #     continue

            logger.info(f"  [MATCHED]         {notice_id} | {title}")
            matched.append({
                'id':           notice_id,
                'name':         release.get('tender', {}).get('title', ''),
                'direct_url':   direct_url,
                'ocds_release': release,
            })

        logger.info(f"--- Filter summary: {len(all_releases)} total | "
                    f"{filter_counts['duplicate']} duplicates | "
                    f"{filter_counts['status_complete']} excluded-status | "
                    f"{filter_counts['country']} excluded-country | "
                    f"{filter_counts['keyword']} no-keyword | "
                    f"{filter_counts['cpv']} no-cpv | "
                    f"{filter_counts['due_date']} out-of-due-date | "
                    f"{len(matched)} matched ---")
        return matched
