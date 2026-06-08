import os
import json

# All shared configuration (DATASET_FIELDS, KEYWORDS, thresholds, Google Sheet
# settings, date helpers, etc.) lives in the root config.py.
# This shim re-exports everything from there and overrides only what is
# specific to the Contracts Finder adapter.
from config import *   # noqa: F401, F403

# Adapter-specific path overrides
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, 'adapter2.log')

# Contracts Finder API and portal settings
CF_API_BASE = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS"
PORTAL_URL  = "https://www.contractsfinder.service.gov.uk/Notice"
PORTAL_NAME = "Contracts-Finder"

# CF is a UK-only portal — country names vary ("England", "ENG", county names, etc.)
# Disable the country filter so no notices are incorrectly excluded.
INCLUDED_COUNTRIES = []

# Read adapter_id from root config.json so it stays in sync with one source of truth
_config_json_path = os.path.join(BASE_DIR, '..', '..', 'config.json')
with open(_config_json_path) as _f:
    _adapters = json.load(_f).get('adapters', [])

_this_adapter = os.path.basename(BASE_DIR).lower()
ADAPTER_ID = next(
    (a['adapter_id'] for a in _adapters if a.get('adapter_id', '').lower() == _this_adapter),
    'NA'
)
