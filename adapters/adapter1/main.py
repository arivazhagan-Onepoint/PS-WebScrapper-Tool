import json
import logging
import os
import sys
from datetime import datetime
from .scraper import TenderScraper
from .tender_parser import TenderParser
from .sheets_writer import SheetsWriter, dedup_by_ocid
from .sc_checker import SCChecker
from .config import LOG_FILE, LOG_FORMAT, BASE_DIR, UK_TIMEZONE, ADAPTER_ID

logger = logging.getLogger(__name__)


def main():
    """Main orchestration function."""
    # Reconfigure stdout to UTF-8 so non-ASCII characters in tender titles
    # don't cause UnicodeEncodeError on Windows consoles (default cp1252).
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        force=True,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    try:
        logger.info("=" * 80)
        logger.info("Starting PS Tender Tracker - Web Scraper")
        logger.info("=" * 80)

        # Step 1: Initialize scraper
        logger.info("\n[1/6] Initializing web scraper...")
        scraper = TenderScraper()

        # Step 2: Scrape tender listings
        logger.info("\n[2/6] Scraping tender listings...")
        tender_summaries = scraper.scrape()

        if not tender_summaries:
            logger.warning("No tenders found matching criteria")
            return

        logger.info(f"Found {len(tender_summaries)} tenders")

        # Step 3: Parse tender details
        logger.info("\n[3/6] Parsing tender details...")
        parser = TenderParser()
        detailed_tenders = []

        for idx, summary in enumerate(tender_summaries, 1):
            logger.info(f"Parsing tender {idx}/{len(tender_summaries)}: {summary.get('id', 'Unknown')}")

            detail = parser.parse_tender_detail(summary)
            if detail:
                detailed_tenders.append(detail)

        logger.info(f"Successfully parsed {len(detailed_tenders)} tender details")

        # Step 3b: Deduplicate by OCID before SC checking to avoid wasted portal fetches
        before = len(detailed_tenders)
        detailed_tenders = dedup_by_ocid(detailed_tenders)
        dropped = before - len(detailed_tenders)
        if dropped:
            logger.info(f"Dedup removed {dropped} duplicate OCID release(s) — {len(detailed_tenders)} unique tenders proceeding to SC check")

        # Step 4: SC Flag — browse each portal page and check for security clearance requirements
        logger.info("\n[4/6] Checking Security Clearance (SC) flag via portal pages...")
        sc_checker = SCChecker()
        sc_found_count = 0

        for idx, tender in enumerate(detailed_tenders, 1):
            tender_id = tender.get('ID', 'Unknown')
            url = tender.get('Direct URL', '')
            logger.info(f"SC check {idx}/{len(detailed_tenders)}: {tender_id}")

            sc_flag, sc_comment = sc_checker.check(url)
            tender['SC_Flag'] = sc_flag

            existing_comments = tender.get('Comments', '')
            tender['Comments'] = (existing_comments + '\n' + sc_comment) if existing_comments else sc_comment

            if sc_flag == 'True':
                sc_found_count += 1

        logger.info(f"SC check complete: {sc_found_count}/{len(detailed_tenders)} tenders flagged")

        # Step 5: Qualify tenders
        logger.info("\n[5/6] Qualifying tenders...")
        pre_qualified = 0
        for tender in detailed_tenders:
            status, reason = parser.qualify_tender(tender)
            tender['Tender Status'] = status
            tender['Tender Qualify Reason'] = reason if status == 'NotQualified' else ''
            ts = datetime.now(UK_TIMEZONE).strftime('%Y-%m-%d %H:%M')
            tender['_qualify_comment'] = f"[{ts}] Tender Status: {status} | {reason}"
            if status == 'PreQualified':
                pre_qualified += 1
        logger.info(f"Qualification complete: {pre_qualified} PreQualified | {len(detailed_tenders) - pre_qualified} NotQualified")

        # Step 5b: Write parsed tenders to JSON file
        json_dir = os.path.join(BASE_DIR, 'target_json')
        os.makedirs(json_dir, exist_ok=True)
        timestamp = datetime.now(UK_TIMEZONE).strftime('%Y%m%d_%H%M%S')
        json_path = os.path.join(json_dir, f"tenders_{timestamp}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(detailed_tenders, f, indent=2, ensure_ascii=False)
        logger.info(f"JSON output written to: {json_path}")

        # Step 6: Write to Google Sheets
        logger.info("\n[6/7] Writing tenders to Google Sheets...")
        writer = SheetsWriter(adapter_id=ADAPTER_ID)
        sheet_id = writer.get_or_create_sheet()

        results = writer.write_batch(detailed_tenders)

        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("SCRAPING COMPLETE - SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total tenders found: {len(tender_summaries)}")
        logger.info(f"Total tenders parsed: {len(detailed_tenders)}")
        logger.info(f"SC Flag - True: {sc_found_count} | False: {len(detailed_tenders) - sc_found_count}")
        logger.info(f"Written to sheet: {results['written']}")
        logger.info(f"Updated in sheet: {results['updated']}")
        logger.info(f"Skipped (within-batch duplicates): {results['skipped_in_batch']}")
        logger.info(f"Errors: {results['errors']}")
        logger.info(f"Sheet ID: {sheet_id}")
        logger.info(f"JSON file: {json_path}")
        logger.info("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

