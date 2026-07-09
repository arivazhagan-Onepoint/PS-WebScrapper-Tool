import argparse
import importlib
import json
import logging
import os
import sys
import traceback
from datetime import datetime

from notifier import send_alert

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(PROJECT_ROOT, 'adapter_config.json'), encoding='utf-8') as f:
        return json.load(f)


def load_project_config():
    with open(os.path.join(PROJECT_ROOT, 'project_config.json'), encoding='utf-8') as f:
        return json.load(f)


def log_project_config():
    proj = load_project_config()
    gs = proj.get('google_sheets', {})
    logger.info("=" * 80)
    logger.info("PROJECT CONFIGURATION")
    logger.info("=" * 80)
    logger.info(f"  Environment     : {gs.get('environment', 'N/A')}")
    logger.info(f"  Sheet Name      : {gs.get('sheet_name', 'N/A')}")
    logger.info(f"  Target Folder ID: {gs.get('target_folder_id', 'N/A')}")
    logger.info("=" * 80)


def run_adapter(adapter_cfg, target_date=None):
    adapter_id = adapter_cfg['adapter_id']
    portal = adapter_cfg['portal']
    logger.info(f"{'=' * 80}")
    logger.info(f"Adapter: {adapter_id} | Portal: {portal} | Type: {adapter_cfg['type']} | Freq: {adapter_cfg['frequency']}")
    logger.info(f"{'=' * 80}")
    module = importlib.import_module(adapter_cfg['module'])
    return module.main(target_date=target_date)


def _build_report(outcomes, started_at, finished_at, target_date, environment='N/A'):
    """Return (subject, html_body) summarising a completed orchestrator run."""
    ran = len(outcomes)
    failed = [o for o in outcomes if o['status'] == 'failed']
    with_errors = [o for o in outcomes if o['status'] == 'success' and o['stats'].get('errors', 0)]

    if failed:
        subject = f"❌ PS WebScrapper Tool [{environment}] — FAILURE ({len(failed)} of {ran} adapter(s) failed)"
        banner_bg = '#c0392b'
    elif with_errors:
        subject = f"⚠️ PS WebScrapper Tool [{environment}] — COMPLETED WITH ERRORS ({ran}/{ran} adapters)"
        banner_bg = '#e67e22'
    else:
        subject = f"✅ PS WebScrapper Tool [{environment}] — SUCCESS ({ran}/{ran} adapters)"
        banner_bg = '#27ae60'

    rows = []
    for o in outcomes:
        aid = o['adapter_id']
        if o['status'] == 'failed':
            first_line = (o['error'].strip().splitlines() or ['(no detail)'])[-1]
            rows.append(
                f"<tr><td>{aid}</td><td style='color:#c0392b;font-weight:bold'>FAILED</td>"
                f"<td colspan='4'>{first_line}</td></tr>"
            )
        else:
            s = o['stats']
            err = s.get('errors', 0)
            err_style = " style='color:#e67e22;font-weight:bold'" if err else ""
            rows.append(
                f"<tr><td>{aid}</td><td style='color:#27ae60;font-weight:bold'>OK</td>"
                f"<td>{s.get('found', 0)}</td><td>{s.get('written', 0)}</td>"
                f"<td>{s.get('updated', 0)}</td><td{err_style}>{err}</td></tr>"
            )
    if not rows:
        rows.append("<tr><td colspan='6'>No adapters ran.</td></tr>")

    failure_details = ""
    for o in failed:
        failure_details += (
            f"<h3 style='margin:16px 0 4px'>{o['adapter_id']} — traceback</h3>"
            f"<pre style='background:#f4f4f4;padding:12px;border-radius:4px;"
            f"overflow-x:auto;font-size:12px'>{o['error']}</pre>"
        )

    html = f"""\
<html><body style="font-family:Arial,Helvetica,sans-serif;color:#222">
  <div style="background:{banner_bg};color:#fff;padding:14px 18px;border-radius:6px;
              font-size:18px;font-weight:bold">{subject}</div>
  <p><b>Environment:</b> {environment}<br>
     <b>Started:</b> {started_at}<br>
     <b>Finished:</b> {finished_at}<br>
     <b>Publication anchor date:</b> {target_date or 'today (default)'}</p>
  <table cellpadding="8" cellspacing="0" border="1"
         style="border-collapse:collapse;border-color:#ddd;font-size:14px">
    <tr style="background:#f0f0f0">
      <th>Adapter</th><th>Status</th><th>Found</th><th>Written</th><th>Updated</th><th>Errors</th>
    </tr>
    {''.join(rows)}
  </table>
  {failure_details}
  <p style="color:#888;font-size:12px;margin-top:20px">
     Automated message from the PS Tender Tracker orchestrator.</p>
</body></html>"""
    return subject, html


def main(adapter_filter=None, target_date=None):
    log_project_config()
    logger.info(f"Publication anchor date: {target_date or 'today (default)'}")
    config = load_config()
    adapters = config.get('adapters', [])
    proj_cfg = load_project_config()
    notify_cfg = proj_cfg.get('notifications', {})
    environment = proj_cfg.get('google_sheets', {}).get('environment', 'N/A')

    if not adapters:
        logger.warning("No adapters configured in adapter_config.json")
        return

    started_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    outcomes = []

    for adapter in adapters:
        if not adapter.get('enabled', True):
            logger.info(f"Skipping disabled adapter: {adapter['adapter_id']}")
            continue
        if adapter_filter and adapter['adapter_id'] != adapter_filter:
            continue

        adapter_id = adapter['adapter_id']
        try:
            stats = run_adapter(adapter, target_date=target_date)
            outcomes.append({'adapter_id': adapter_id, 'status': 'success',
                             'stats': stats or {}})
        except Exception:
            tb = traceback.format_exc()
            logger.error(f"Adapter '{adapter_id}' failed:\n{tb}")
            outcomes.append({'adapter_id': adapter_id, 'status': 'failed',
                             'error': tb})

    finished_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subject, html = _build_report(outcomes, started_at, finished_at, target_date, environment)
    send_alert(subject, html, notify_cfg)
    return outcomes


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    parser = argparse.ArgumentParser(description="PS Tender Tracker orchestrator")
    parser.add_argument(
        'adapter', nargs='?', default=None,
        help="Optional adapter_id to run a single adapter (e.g. Adapter1)"
    )
    parser.add_argument(
        '--date', dest='date', default=None,
        help="Publication anchor date as YYYY-MM-DD; defaults to today"
    )
    args = parser.parse_args()

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            parser.error(f"Invalid --date '{args.date}'. Expected format YYYY-MM-DD.")

    outcomes = main(adapter_filter=args.adapter, target_date=target_date)

    # Preserve non-zero exit on failure so schedulers (Task Scheduler / cron)
    # still register the run as failed, in addition to the email alert.
    if outcomes and any(o['status'] == 'failed' for o in outcomes):
        sys.exit(1)
