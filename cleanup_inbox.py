"""
cleanup_inbox.py — Permanently delete AgentMail threads older than 7 days.

Reads SENDER_INBOX_ID from .env — no hardcoded addresses.

Cron suggestion (runs daily after the main crew, adjust path as needed):
  0 8 * * * cd /home/ubuntu/alabama && python cleanup_inbox.py >> logs/cleanup.log 2>&1
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from agentmail import AgentMail

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ─── Logging ──────────────────────────────────────────────────────────────────

log_path = os.path.join(os.path.dirname(__file__), "logs", "cleanup.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Validate environment ─────────────────────────────────────────────────────

REQUIRED_ENV = ["AGENTMAIL_API_KEY", "SENDER_INBOX_ID"]
missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if missing:
    log.error(f"Missing required environment variables: {missing}")
    sys.exit(1)

inbox_id = os.environ["SENDER_INBOX_ID"]
cutoff = datetime.now(timezone.utc) - timedelta(days=7)

# ─── Cleanup ──────────────────────────────────────────────────────────────────

client = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])

log.info(f"=== Inbox cleanup starting — inbox: {inbox_id} | cutoff: {cutoff.isoformat()} ===")

deleted = 0
errors = 0
page_token = None

while True:
    try:
        kwargs = dict(
            inbox_id=inbox_id,
            before=cutoff,
            limit=50,
            include_spam=True,
            include_trash=True,
        )
        if page_token:
            kwargs["page_token"] = page_token

        result = client.inboxes.threads.list(**kwargs)
        threads = result.threads

        if not threads:
            break

        for thread in threads:
            try:
                client.inboxes.threads.delete(
                    inbox_id=inbox_id,
                    thread_id=thread.thread_id,
                    permanent=True,
                )
                deleted += 1
                log.debug(f"  Deleted thread {thread.thread_id} ({thread.subject or '(no subject)'})")
            except Exception as e:
                errors += 1
                log.warning(f"  Failed to delete thread {thread.thread_id}: {e}")

        page_token = getattr(result, "next_page_token", None)
        if not page_token:
            break

    except Exception as e:
        log.error(f"Error listing threads: {e}")
        break

log.info(f"=== Cleanup complete — {deleted} deleted, {errors} errors ===")
