import json
import logging
import os
import re
from datetime import datetime
from .config import UK_TIMEZONE, PORTAL_NAME, ADAPTER_ID, get_due_date_range

logger = logging.getLogger(__name__)

# Load CPV descriptions from the project-root reference file
_CPV_DESC_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'cpv_description.json')
try:
    with open(_CPV_DESC_PATH, encoding='utf-8') as _f:
        _CPV_DESCRIPTIONS = {
            entry['code']: entry['description']
            for entry in json.load(_f).get('cpv_codes', [])
        }
except Exception:
    _CPV_DESCRIPTIONS = {}


class TenderParser:

    def parse_tender_detail(self, summary):
        """Parse an OCDS release dict (from scraper) into the dataset row format."""
        try:
            release = summary.get('ocds_release', {})
            tender  = release.get('tender', {})
            direct_url = summary.get('direct_url', '')

            tender_data = {
                'Portal Name':           PORTAL_NAME,
                'Adapter':               ADAPTER_ID,
                'Direct URL':            direct_url,
                'Published On':          self._published_date(release),
                'ID':                    self._notice_id(release, direct_url),
                'OCID':                  release.get('ocid', ''),
                'Name':                  tender.get('title', ''),
                'Tender Due Date':       self._format_date(self._due_date(tender, release)),
                'Clarification Due Date': self._format_date(tender.get('enquiryPeriod', {}).get('endDate', '')),
                'Procurement Stage':     self._procurement_stage(release),
                'Total Contract Value':  self._value(tender, release),
                'Contract Duration':     self._duration(tender, release),
                'Annual Contract Value': self._annual_value(tender, self._value(tender, release), release),
                'Tender Description':    tender.get('description', ''),
                'Buyer Name':            self._buyer_name(release),
                'Suitable for SMEs?':    self._sme_flag(tender),
                'Tender Status':                tender.get('status', ''),
                'Tender Status Date':           self._status_date(release),
                'Processed Date':        datetime.now(UK_TIMEZONE).isoformat(),
                'Comments':              '',  # set below after all fields are known
                'Last Modified Date':    '',
                'Created Date':          '',
                'CPV Code':              self._cpv_codes(tender),
                'CPV Description':       self._cpv_descriptions(tender),
                'SC_Flag':               'TBD',
                'Country':               self._country(release),
                'Locality':              self._locality(release)
            }

            tender_data['Comments'] = self._build_comment(tender_data)

            logger.info(
                f"  [PARSED] {tender_data['ID']} | "
                f"Name: {tender_data['Name'][:50]} | "
                f"Published: {tender_data['Published On'] or '-'} | "
                f"Due: {tender_data['Tender Due Date'] or '-'} | "
                f"Stage: {tender_data['Procurement Stage'][:30] or '-'} | "
                f"Value: {tender_data['Total Contract Value'] or '-'} | "
                f"Duration: {tender_data['Contract Duration'] or '-'} | "
                f"SME: {tender_data['Suitable for SMEs?']} | "
                f"Status: {tender_data['Tender Status'] or '-'} | "
                f"Buyer: {tender_data['Buyer Name'][:40] or '-'}"
            )
            return tender_data

        except Exception as e:
            logger.error(f"Error parsing tender detail: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------ #
    # Field helpers
    # ------------------------------------------------------------------ #

    def qualify_tender(self, tender_data):
        """
        Qualification rules (applied last, after all fields are populated).
        Returns (status, reason) where status is 'PreQualified' or 'NotQualified'
        and reason is a human-readable explanation of the decision.

          Value/stage rules:
            1. planning stage  + annual value < £1,000,000  → candidate for PreQualified
            2. tender/opportunity stage + annual value < £139,689 → candidate for PreQualified
            3. annual value missing/unparseable              → candidate for PreQualified
            All other value/stage scenarios                 → NotQualified
          Due date rule (applied only when value/stage passes):
            4. due date within window (get_due_date_range)  → PreQualified
               due date present but outside window          → NotQualified
               due date missing/unparseable                 → PreQualified (benefit of the doubt)
        """
        stage = (tender_data.get('Procurement Stage') or '').lower()
        annual_str = (tender_data.get('Annual Contract Value') or '').strip()

        # Parse numeric annual value from strings like "GBP 139,000.00"
        annual_val = None
        if annual_str:
            try:
                annual_val = float(annual_str.split()[-1].replace(',', ''))
            except (ValueError, IndexError):
                annual_val = None

        # Determine whether value/stage rules pass and build reason
        if annual_val is None:
            value_qualifies = True
            reason = "Annual value not available — benefit of the doubt"
        elif 'planning' in stage:
            value_qualifies = annual_val < 1_000_000
            threshold = '£1,000,000'
            op = '<' if value_qualifies else '>='
            reason = f"Planning stage | Annual value {annual_str} {op} {threshold}"
        elif any(kw in stage for kw in ['tender', 'opportunity']):
            value_qualifies = annual_val < 139_689
            threshold = '£139,689'
            op = '<' if value_qualifies else '>='
            reason = f"Tender/Opportunity stage | Annual value {annual_str} {op} {threshold}"
        else:
            value_qualifies = False
            reason = f"Stage '{stage or 'unknown'}' is not planning/tender/opportunity"

        if not value_qualifies:
            return 'NotQualified', reason

        # Due date check — only reached when value/stage rules pass
        due_date_str = (tender_data.get('Tender Due Date') or '').strip()
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                due_start, _ = get_due_date_range()
                if due_date < due_start:
                    reason += f" | Due date {due_date_str} is before window start [{due_start}]"
                    return 'NotQualified', reason
            except Exception:
                pass  # unparseable due date — no penalty

        return 'PreQualified', reason

    def _procurement_stage(self, release):
        tags = release.get('tag', [])
        initiation = release.get('initiationType', '')
        tag_str = ', '.join(tags) if tags else ''
        if tag_str and initiation:
            return f"{tag_str} / {initiation}"
        return tag_str or initiation

    def _cpv_codes(self, tender):
        codes = []

        def _add(cl):
            if cl.get('scheme') == 'CPV' and cl.get('id'):
                code = str(cl['id'])
                if code not in codes:
                    codes.append(code)

        # tender.classification (top-level)
        _add(tender.get('classification', {}))

        # tender.items[].classification + additionalClassifications
        for item in tender.get('items', []):
            _add(item.get('classification', {}))
            for ac in item.get('additionalClassifications', []):
                _add(ac)

        # tender.lots[].items[].classification + additionalClassifications
        for lot in tender.get('lots', []):
            for item in lot.get('items', []):
                _add(item.get('classification', {}))
                for ac in item.get('additionalClassifications', []):
                    _add(ac)

        return ', '.join(codes)

    def _cpv_descriptions(self, tender):
        """Return formatted 'code - description' strings for all CPV codes on the tender."""
        seen = set()
        descriptions = []

        def _add(cl):
            if cl.get('scheme') == 'CPV' and cl.get('id'):
                code = str(cl['id'])
                if code not in seen:
                    seen.add(code)
                    desc = _CPV_DESCRIPTIONS.get(code, '')
                    descriptions.append(f"{code} - {desc}" if desc else code)

        _add(tender.get('classification', {}))
        for item in tender.get('items', []):
            _add(item.get('classification', {}))
            for ac in item.get('additionalClassifications', []):
                _add(ac)
        for lot in tender.get('lots', []):
            for item in lot.get('items', []):
                _add(item.get('classification', {}))
                for ac in item.get('additionalClassifications', []):
                    _add(ac)

        return ', '.join(descriptions)

    def _build_comment(self, td):
        ts = datetime.now(UK_TIMEZONE).strftime('%Y-%m-%d %H:%M')
        parts = [f"[{ts}] First scraped"]
        for label, key in [
            ('Pub',    'Published On'),
            ('Due',    'Tender Due Date'),
            ('Val',    'Total Contract Value'),
            ('Stage',  'Procurement Stage'),
            ('Status', 'Tender Status'),
            ('SME',    'Suitable for SMEs?'),
            ('Buyer',  'Buyer Name'),
        ]:
            val = str(td.get(key, '')).strip()
            if val:
                parts.append(f"{label}:{val[:40]}")
        return ' | '.join(parts)

    def _due_date(self, tender, release):
        """Return the best available due date using four fallback priorities:
          1. tender.tenderPeriod.endDate          — official bid closing date
          2. planning.milestones[].dueDate        — milestone date on pre-market notices
          3. tender.expressionOfInterestDeadline  — EOI deadline on early-stage notices
          4. tender.communication.futureNoticeDate — anticipated tender publication date
        """
        # Priority 1
        end = tender.get('tenderPeriod', {}).get('endDate', '')
        if end:
            return end

        # Priority 2
        for milestone in release.get('planning', {}).get('milestones', []):
            due = milestone.get('dueDate', '')
            if due:
                return due

        # Priority 3
        eoi = tender.get('expressionOfInterestDeadline', '')
        if eoi:
            return eoi

        return ''

    def _published_date(self, release):
        raw = release.get('date') or release.get('publishedDate', '')
        return self._format_date(raw)

    def _notice_id(self, release, url=''):
        # Release-level 'id' contains the notice number e.g. 045337-2026
        notice_id = release.get('id', '')
        if notice_id:
            return notice_id
        # Fall back to URL path
        url_match = re.search(r'/Notice/([^/?#]+)', url, re.IGNORECASE)
        return url_match.group(1) if url_match else 'Unknown'

    def _buyer_name(self, release):
        buyer_ref = release.get('buyer', {})
        buyer_id  = buyer_ref.get('id', '')
        buyer_name = buyer_ref.get('name', '')
        if buyer_name:
            return buyer_name
        # Look up in parties list
        for party in release.get('parties', []):
            if party.get('id') == buyer_id or 'buyer' in party.get('roles', []):
                return party.get('name', '')
        return ''

    def _country(self, release):
        buyer_id = release.get('buyer', {}).get('id', '')
        for party in release.get('parties', []):
            if party.get('id') == buyer_id or 'buyer' in party.get('roles', []):
                return party.get('address', {}).get('countryName', '')
        return ''

    def _locality(self, release):
        buyer_id = release.get('buyer', {}).get('id', '')
        for party in release.get('parties', []):
            if party.get('id') == buyer_id or 'buyer' in party.get('roles', []):
                return party.get('address', {}).get('locality', '')
        return ''

    def _sme_flag(self, tender):
        if tender.get('suitability', {}).get('sme'):
            return 'Yes'
        for lot in tender.get('lots', []):
            if lot.get('suitability', {}).get('sme'):
                return 'Yes'
        return 'No'

    def _extract_amount(self, value_dict):
        """Return (amount, currency) from a value dict, preferring amountGross over amount."""
        if not value_dict:
            return None, 'GBP'
        amt = value_dict.get('amountGross') if value_dict.get('amountGross') is not None else value_dict.get('amount')
        return amt, value_dict.get('currency', 'GBP')

    def _value(self, tender, release=None):
        # 1. tender.value
        amt, currency = self._extract_amount(tender.get('value', {}))
        if amt is not None:
            return f"{currency} {amt:,.2f}"
        # 2. Sum tender.lots[].value
        total, currency = 0, 'GBP'
        for lot in tender.get('lots', []):
            amt, cur = self._extract_amount(lot.get('value', {}))
            if amt is not None:
                total += amt; currency = cur
        if total:
            return f"{currency} {total:,.2f}"
        if release:
            # 3. Sum release.awards[].value
            total, currency = 0, 'GBP'
            for award in release.get('awards', []):
                amt, cur = self._extract_amount(award.get('value', {}))
                if amt is not None:
                    total += amt; currency = cur
            if total:
                return f"{currency} {total:,.2f}"
            # 4. Sum release.contracts[].value
            total, currency = 0, 'GBP'
            for contract in release.get('contracts', []):
                amt, cur = self._extract_amount(contract.get('value', {}))
                if amt is not None:
                    total += amt; currency = cur
            if total:
                return f"{currency} {total:,.2f}"
        return ''

    def _find_contract_period(self, tender, release):
        """Locate the best contractPeriod dict from tender lots, awards, contracts, or tender-level."""
        # 1. tender.lots[].contractPeriod  (most common in FTS API)
        for lot in tender.get('lots', []):
            cp = lot.get('contractPeriod', {})
            if cp.get('startDate') or cp.get('endDate') or cp.get('durationInMonths'):
                return cp
        # 2. release.awards[].contractPeriod
        for award in release.get('awards', []):
            cp = award.get('contractPeriod', {})
            if cp.get('startDate') or cp.get('endDate') or cp.get('durationInMonths'):
                return cp
        # 3. release.contracts[].period
        for contract in release.get('contracts', []):
            cp = contract.get('period', {})
            if cp.get('startDate') or cp.get('endDate') or cp.get('durationInMonths'):
                return cp
        # 4. tender.contractPeriod (fallback)
        return tender.get('contractPeriod', {})

    def _duration_months(self, tender, release=None):
        """Return (base_months, extension_months) derived from contractPeriod."""
        period = self._find_contract_period(tender, release or {})
        start   = period.get('startDate', '')
        end     = period.get('endDate', '')
        max_ext = period.get('maxExtentDate', '')
        raw_months = period.get('durationInMonths')

        _avg_days_per_month = 365.25 / 12  # accounts for leap years

        base_months = 0
        if raw_months:
            base_months = int(raw_months)
        elif start and end:
            try:
                s = datetime.fromisoformat(start.replace('Z', '+00:00'))
                e = datetime.fromisoformat(end.replace('Z', '+00:00'))
                base_months = round((e - s).days / _avg_days_per_month)
            except Exception:
                pass

        ext_months = 0
        if max_ext and end:
            try:
                e = datetime.fromisoformat(end.replace('Z', '+00:00'))
                m = datetime.fromisoformat(max_ext.replace('Z', '+00:00'))
                ext_months = round((m - e).days / _avg_days_per_month)
            except Exception:
                pass

        return base_months, ext_months

    def _duration(self, tender, release=None):
        base, ext = self._duration_months(tender, release)
        period = self._find_contract_period(tender, release or {})
        start  = period.get('startDate', '')
        end    = period.get('endDate', '')

        if base:
            if ext:
                return f"{base} months (+ up to {ext} months extension)"
            return f"{base} months"
        if start and end:
            return f"{start[:10]} to {end[:10]}"
        if end:
            return f"Until {end[:10]}"
        return ''

    def _annual_value(self, tender, total_value_str, release=None):
        if not total_value_str:
            return ''
        base_months, _ = self._duration_months(tender, release)
        if not base_months:
            return ''
        try:
            parts = total_value_str.split()
            currency = parts[0] if len(parts) >= 2 else 'GBP'
            amount = float(parts[-1].replace(',', ''))
            annual = amount / (base_months / 12)
            return f"{currency} {annual:,.2f}"
        except Exception:
            return ''

    def _status_date(self, release):
        # Award date if available
        for award in release.get('awards', []):
            date = award.get('date', '')
            if date:
                return self._format_date(date)
        return ''

    def _format_date(self, date_str):
        if not date_str:
            return ''
        # Strip trailing time component
        date_str = re.sub(r'T.*$', '', str(date_str).strip())
        for pattern in ('%Y-%m-%d', '%d/%m/%Y', '%d %B %Y', '%d %b %Y', '%d-%m-%Y'):
            try:
                return datetime.strptime(date_str, pattern).strftime('%Y-%m-%d')
            except ValueError:
                continue
        return date_str
