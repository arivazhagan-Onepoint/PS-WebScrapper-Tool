import logging
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from .config import UK_TIMEZONE

logger = logging.getLogger(__name__)

# Ordered from most to least specific so deduplication keeps the right label.
SC_CLEARANCE_PATTERNS = [
    # Full phrases
    (r'enhanced\s+developed\s+vetting',              'Enhanced Developed Vetting (eDV)'),
    (r'enhanced\s+security\s+check',                 'Enhanced Security Check (eSC)'),
    (r'counter[\s\-]terrorist\s+check',              'Counter Terrorist Check (CTC)'),
    (r'developed\s+vetting',                         'Developed Vetting (DV)'),
    (r'security\s+check',                            'Security Check (SC)'),
    # Abbreviations — unambiguous ones matched unconditionally
    (r'\beDV\b',                                     'Enhanced Developed Vetting (eDV)'),
    (r'\beSC\b',                                     'Enhanced Security Check (eSC)'),
    (r'\bCTC\b',                                     'Counter Terrorist Check (CTC)'),
    # DV / SC are too generic — only match when paired with clearance/vetting context
    (r'\bDV\s+(?:clearance|vetted|vetting|level)\b', 'Developed Vetting (DV)'),
    (r'\bSC\s+(?:clearance|vetted|vetting|level)\b', 'Security Check (SC)'),
    (r'(?:clearance|vetting)\s+(?:level\s+)?DV\b',   'Developed Vetting (DV)'),
    (r'(?:clearance|vetting)\s+(?:level\s+)?SC\b',   'Security Check (SC)'),
]

_REQUEST_DELAY = 1.2  # polite delay between portal fetches (seconds)


class SCChecker:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-GB,en;q=0.9',
        })
        self._last_request_at = 0.0

    def check(self, url):
        """
        Fetch the portal page for ``url`` and scan for security clearance requirements.

        Returns a tuple (sc_flag, comment):
          sc_flag — 'Yes' if any clearance term found, 'No' if page loaded
                    but no terms found, 'TBD' if page could not be fetched.
          comment — timestamped human-readable explanation.
        """
        ts = datetime.now(UK_TIMEZONE).strftime('%Y-%m-%d %H:%M')

        if not url:
            return 'TBD', f'[{ts}] SC check: skipped — no URL available'

        html = self._fetch(url)
        if html is None:
            return 'TBD', f'[{ts}] SC check: skipped — portal page could not be fetched'

        page_text = self._extract_text(html)
        found = self._scan(page_text)

        if found:
            detail = ', '.join(found)
            logger.info(f'SC check FOUND [{url}]: {detail}')
            return 'Yes', f'[{ts}] SC check: FOUND — {detail}'

        logger.info(f'SC check CLEAR [{url}]')
        return 'No', f'[{ts}] SC check: No security clearance requirement mentioned on portal page'

    def _fetch(self, url, max_retries=3):
        """HTTP GET with courtesy delay and retry on transient errors."""
        elapsed = time.time() - self._last_request_at
        if elapsed < _REQUEST_DELAY:
            time.sleep(_REQUEST_DELAY - elapsed)

        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=25)
                self._last_request_at = time.time()

                if response.status_code == 429:
                    wait = int(response.headers.get('Retry-After', 15 * (attempt + 1)))
                    logger.warning(f'SC check rate limited — waiting {wait}s (attempt {attempt + 1}/{max_retries})')
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                return response.text

            except requests.RequestException as e:
                logger.warning(f'SC check fetch error (attempt {attempt + 1}/{max_retries}): {e}')
                if attempt < max_retries - 1:
                    time.sleep(5)

        return None

    def _extract_text(self, html):
        """Parse HTML and return clean plain text with script/style removed."""
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript', 'meta', 'head']):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)

    def _scan(self, text):
        """Return a deduplicated, ordered list of clearance labels found in ``text``."""
        found = []
        seen = set()
        for pattern, label in SC_CLEARANCE_PATTERNS:
            if label not in seen and re.search(pattern, text, re.IGNORECASE):
                found.append(label)
                seen.add(label)
        return found
