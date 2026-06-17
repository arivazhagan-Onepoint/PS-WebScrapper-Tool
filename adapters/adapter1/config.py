import os
import json

# All shared configuration (DATASET_FIELDS, Google Sheet settings, etc.)
# lives in the root config.py. This shim re-exports everything from there
# and overrides with adapter1-specific settings loaded from adapter_config.json.
from config import *   # noqa: F401, F403
from datetime import datetime, timedelta
import holidays as _holidays

# Adapter-specific path overrides
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, 'adapter1.log')

# Load this adapter's config section from adapter_config.json
_config_path = os.path.join(BASE_DIR, '..', '..', 'adapter_config.json')
with open(_config_path, encoding='utf-8') as _f:
    _adapters = json.load(_f).get('adapters', [])

_this_adapter = os.path.basename(BASE_DIR).lower()
_cfg = next(
    (a for a in _adapters if a.get('adapter_id', '').lower() == _this_adapter),
    {}
)

ADAPTER_ID = _cfg.get('adapter_id', 'NA')

# Filters
FTS_API_KEY        = _cfg.get('fts_api', {}).get('api_key')
CPV_CODES          = _cfg['filters']['cpv_codes']
SUITABLE_FOR_SMES  = _cfg['filters']['suitable_for_smes']
EXCLUDED_STATUSES  = _cfg['filters']['excluded_statuses']
EXCLUDED_TAGS      = set(_cfg['filters']['excluded_tags'])
INCLUDED_COUNTRIES = _cfg['filters']['included_countries']
KEYWORDS           = _cfg['filters']['keywords']

# Qualification thresholds
PLANNING_THRESHOLD = _cfg['qualification_thresholds']['planning_stage_max_annual_value']
TENDER_THRESHOLD   = _cfg['qualification_thresholds']['tender_stage_max_annual_value']

# Date windows
_pub_days  = _cfg['date_windows']['publication_lookback_days']
_due_days  = _cfg['date_windows']['due_date_min_working_days']


def get_publication_date_range():
    today = datetime.now(UK_TIMEZONE).date()
    return today - timedelta(days=_pub_days), today


def get_due_date_range():
    """Returns due_start: tenders closing within the configured UK working days are excluded."""
    today = datetime.now(UK_TIMEZONE).date()
    uk_holidays = _holidays.UnitedKingdom(subdiv="ENG")
    count = 0
    d = today
    while count < _due_days:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in uk_holidays:
            count += 1
    return d
