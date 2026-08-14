"""
run_crew.py — Entry point for daily newsletter briefing crew.
Pre-processes all emails via direct GPT-4o API call,
then uses crew only for email composition and tweets.
"""

import os
import sys
import json
import logging
import shutil
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ─── Sophie notification helper ───────────────────────────────────────────────

import requests as _req

def notify_sophie(message: str) -> None:
    """Fire-and-forget POST to Sophie's notify server on localhost:5555."""
    agent_name = os.environ.get("AGENT_NAME", "Alabama")
    try:
        _req.post(
            "http://localhost:5555/notify",
            json={"message": f"[{agent_name}] {message}"},
            timeout=5,
        )
    except Exception as e:
        log.warning(f"notify_sophie failed (non-fatal): {e}")

# ─── Logging ──────────────────────────────────────────────────────────────────


log_path = os.path.join(os.path.dirname(__file__), "logs", "crew.log")
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

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _enabled(var: str, default: bool = True) -> bool:
    """Read a boolean env var. Absent → default. 'true'/'1'/'yes' → True. Anything else → False."""
    val = os.environ.get(var)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")

# ─── Validate environment ─────────────────────────────────────────────────────

REQUIRED_ENV = ["OPENAI_API_KEY"]

# SMTP vars only required when EMAIL_ENABLED (default true)
if _enabled("EMAIL_ENABLED"):
    REQUIRED_ENV += ["RECIPIENT_EMAIL", "SENDER_EMAIL", "SENDER_APP_PASSWORD"]

missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if missing:
    log.error(f"Missing required environment variables: {missing}")
    sys.exit(1)

# ─── Today's date ─────────────────────────────────────────────────────────────

try:
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
except ImportError:
    et = timezone(timedelta(hours=-4))

today_et = datetime.now(et).strftime("%B %d, %Y")
today_iso = datetime.now(et).strftime("%Y-%m-%d")

log.info(f"=== Daily Newsletter Crew starting — {datetime.now().isoformat()} ===")
log.info(f"=== Today: {today_et} ===")

# ─── Step 1: Determine fetch window ───────────────────────────────────────────
# CRITICAL: Read the old timestamp BEFORE writing the new one.

last_run_path = os.path.join(os.path.dirname(__file__), "last_run.json")
backup_path = last_run_path + ".bak"

# Read previous run timestamp first
since = datetime.now(timezone.utc) - timedelta(hours=36)  # safe default
try:
    with open(last_run_path, "r") as f:
        last_run = json.load(f)
    since = datetime.fromisoformat(last_run["last_run_at"]) - timedelta(hours=12)
    log.info(f"Using last_run timestamp (with 12h overlap): {since.isoformat()}")
    #since = datetime.fromisoformat(last_run["last_run_at"])
    #log.info(f"Using last_run timestamp: {since.isoformat()}")
except FileNotFoundError:
    log.info(f"No last_run.json found — using 24h fallback: {since.isoformat()}")
except Exception as e:
    log.warning(f"Could not read last_run.json ({e}) — using 24h fallback: {since.isoformat()}")

# Back up old file before overwriting
try:
    if os.path.exists(last_run_path):
        shutil.copy2(last_run_path, backup_path)
except Exception as e:
    log.warning(f"Could not back up last_run.json: {e}")

# Now write current timestamp for the NEXT run
with open(last_run_path, "w") as f:
    json.dump({
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "today_date": today_et,
    }, f, indent=2)

log.info(f"Fetch window: {since.isoformat()} → now")

# ─── Step 2: Fetch emails ─────────────────────────────────────────────────────

import re

def strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()

def clean_json(raw: str) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)

emails = []
agentmail_key = os.environ.get("AGENTMAIL_API_KEY", "")
inbox_id = os.environ.get("SENDER_INBOX_ID", "")

if agentmail_key and inbox_id:
    try:
        from agentmail import AgentMail
        client_am = AgentMail(api_key=agentmail_key)
        threads = client_am.inboxes.threads.list(
            inbox_id=inbox_id,
            after=since,
            limit=50,
            include_spam=True,
        )
        log.info(f"Fetched {len(threads.threads)} email threads from AgentMail")
        for thread in threads.threads:
            try:
                detail = client_am.inboxes.threads.get(
                    inbox_id=inbox_id,
                    thread_id=thread.thread_id,
                )
                msg = detail.messages[0] if detail.messages else None
                if not msg:
                    continue
                body = msg.text or ""
                if not body and msg.html:
                    body = strip_html(msg.html)
                body = clean_json(body)
                emails.append({
                    "thread_id": thread.thread_id,
                    "subject": msg.subject or "(no subject)",
                    "from": msg.from_ or "",
                    "body_text": body[:6000],
                })
            except Exception as e:
                log.warning(f"Error reading thread {thread.thread_id}: {e}")
        log.info(f"Successfully read {len(emails)} emails")
    except Exception as e:
        log.warning(f"AgentMail fetch failed (non-fatal): {e}", exc_info=True)
else:
    log.info("AGENTMAIL_API_KEY or SENDER_INBOX_ID not set — skipping newsletter email fetch")

total_fetched = len(emails)

# ─── Step 2b: Fetch web sources ───────────────────────────────────────────────

owned_source_labels: set[str] = set()
try:
    from fetch_web_sources import fetch_web_sources, get_owned_urls
    web_items = fetch_web_sources(since=since)
    if web_items:
        emails.extend(web_items)
        owned_source_labels = {
            item["from"] for item in web_items if item.get("is_owned_source")
        }
        log.info(
            f"Added {len(web_items)} item(s) from web sources — "
            f"{len(owned_source_labels)} owned source label(s) — "
            f"total inputs: {len(emails)}"
        )
except Exception as e:
    log.warning(f"Web source fetch failed (non-fatal): {e}", exc_info=True)

# ─── Step 3: Extract stories via GPT-4o ──────────────────────────────────────
# GPT-4o returns ALL relevant stories (any grade).
# Grade filtering happens in Python so we can count accurately.

all_extracted_stories = []
stories = []  # grade >= 5

if emails:
    import anthropic as _anthropic
    import openai
    from alabama_core.config import get_categories, get_subcategories, get_all_codes
    from alabama_core.extract_stories import extract_stories as _extract_stories

    oc = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # EXTRACTION_MODEL=haiku uses Claude Haiku 4.5; default is GPT-4o (preserves prior behaviour)
    _extraction_model = os.environ.get("EXTRACTION_MODEL", "").strip().lower()
    _ac_extract = (
        _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        if _extraction_model == "haiku" and os.environ.get("ANTHROPIC_API_KEY")
        else None
    )

    # Load existing stories for deduplication
    state_path = os.path.join(os.path.dirname(__file__), "daily_brief.json")
    existing_sources = []
    fresh = []
    try:
        with open(state_path, "r") as f:
            existing = json.load(f)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        fresh = [
            s for s in existing.get("stories", [])
            if datetime.fromisoformat(
                s.get("timestamp", "2000-01-01").replace("Z", "+00:00")
            ) > cutoff
        ]
        existing_sources = [s.get("source", "") for s in fresh if s.get("source")]
        log.info(f"Loaded {len(fresh)} existing stories for deduplication ({len(existing_sources)} sources)")
    except FileNotFoundError:
        log.info("No existing state file — starting fresh")
    except Exception as e:
        log.warning(f"Could not load state: {e}")

    _grade_criteria = os.environ.get("GRADE_CRITERIA", "business impact + novelty")
    _cats = get_categories()
    _subcats = get_subcategories()
    _codes = ",".join(get_all_codes())

    all_extracted_stories = _extract_stories(
        items=emails,
        cats=_cats,
        subcats=_subcats,
        codes=_codes,
        grade_criteria=_grade_criteria,
        existing_sources=existing_sources,
        openai_client=oc,
        anthropic_client=_ac_extract,
        use_haiku=bool(_ac_extract),
    )

    # Filter by grade in Python — gives accurate counts
    stories = [s for s in all_extracted_stories if int(s.get("grade", 0)) >= 5]

    log.info(
        f"Story extraction complete: "
        f"{total_fetched} emails fetched | "
        f"{len(all_extracted_stories)} stories extracted | "
        f"{len(stories)} stories included (grade>=5)"
    )

    # Save state — store only grade>=5 stories
    all_stories = fresh + stories
    try:
        with open(state_path, "w") as f:
            json.dump({
                "stories": all_stories,
                "updated": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
        log.info(f"State saved: {len(all_stories)} total stories on disk")
    except Exception as e:
        log.error(f"State save failed: {e}")

# ─── Step 4: Write and push tweets directly via Python ───────────────────────

import importlib.util as _ilu
_bt = _ilu.spec_from_file_location("buffer_tool", os.path.join(os.path.dirname(__file__), "..", "shared", "tools", "buffer_tool.py"))
_bm = _ilu.module_from_spec(_bt); _bt.loader.exec_module(_bm)
_push_to_buffer = _bm.push_to_buffer


def push_tweet_to_buffer(tweet_text: str) -> bool:
    token      = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    channel_id = os.environ.get("BUFFER_CHANNEL_ID", "")
    draft_path = os.path.join(os.path.dirname(__file__), "tweets_draft.txt")
    ok = _push_to_buffer(tweet_text, channel_id, token, label="tweet", draft_path=draft_path)
    if not ok:
        notify_sophie(f"Buffer push failed (tweet). Draft saved to tweets_draft.txt.")
    return ok


def write_tweets(stories: list, oc, owned_source_labels: set | None = None) -> list:
    """Ask GPT-4o to write 2 tweets for the top 2 stories. Returns list of tweet strings."""
    if not stories:
        return []
    top2 = sorted(stories, key=lambda s: int(s.get("grade", 0)), reverse=True)[:2]
    results = []
    for story in top2:
        url = story.get("source", "")
        # Twitter wraps every URL to exactly 23 chars via t.co, regardless of actual length
        TCO_LEN = 23
        url_budget = (TCO_LEN + 1) if url else 0  # +1 for the space before URL
        max_text_len = 140 - url_budget if url else 140
        is_owned = (
            story.get("is_owned_source")
            or (owned_source_labels and story.get("from_newsletter", "") in owned_source_labels)
        )
        if is_owned:
            voice_rule = "- This is the author's OWN content. Write in first person ('I', 'my'). Tone: personal and inviting, promoting the author's work.\n"
        else:
            voice_rule = "- Factual and punchy. Third person. No hashtags. No emojis.\n"
        prompt = (
            f"Write a single tweet for this story. Rules:\n"
            f"{voice_rule}"
            f"- End with the source URL: {url}\n"
            f"- Twitter shortens every URL to 23 characters automatically. So your tweet text "
            f"(before the URL) must be {max_text_len} characters or fewer, and the full tweet "
            f"(text + space + URL) must be 140 characters or fewer as Twitter counts it.\n"
            f"- Write {max_text_len} chars of text maximum, then a space, then the URL. Nothing else.\n"
            "- Return ONLY the final tweet text (including the URL), nothing else.\n\n"
            f"Story summary: {story.get('summary', '')}"
        )
        tweet = None
        for attempt in range(5):
            try:
                resp = oc.chat.completions.create(
                    model="gpt-4o",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                )
                candidate = resp.choices[0].message.content.strip().strip('"')
                # Measure Twitter length: URLs count as 23 chars regardless of actual length
                twitter_len = len(candidate)
                if url and url in candidate:
                    twitter_len = twitter_len - len(url) + TCO_LEN
                if twitter_len <= 140:
                    tweet = candidate
                    log.info(f"Tweet written ({twitter_len} Twitter chars, {len(tweet)} raw): {tweet}")
                    break
                else:
                    log.warning(f"Tweet too long ({twitter_len} Twitter chars), retrying... (attempt {attempt+1}/5)")
                    text_part = candidate.replace(url, "").strip()
                    log.warning(f"Text part is {len(text_part)} chars, max is {max_text_len}")
                    prompt += (
                        f"\n\nAttempt {attempt+1}: text before URL was {len(text_part)} chars "
                        f"but must be {max_text_len} or fewer. Cut ruthlessly."
                    )
            except Exception as e:
                log.error(f"Tweet generation failed (attempt {attempt+1}): {e}")
        if tweet is None:
            # All retries exhausted — hard-truncate rather than drop the tweet entirely
            fallback = candidate if "candidate" in dir() else ""
            if fallback:
                tweet = fallback[:137] + "…"
                log.warning(f"All retries failed — hard-truncating to 140 chars: {tweet}")
            else:
                log.error("Tweet generation produced no output — skipping this story.")
                continue
        results.append(tweet)
    return results


log.info("=== Step 4: Writing and pushing tweets ===")
tweet_texts = []
tweet_push_results = []  # parallel list: True/False per tweet
if stories and _enabled("TWITTER_ENABLED"):
    tweet_texts = write_tweets(stories, oc, owned_source_labels=owned_source_labels)
    log.info(f"Tweets written: {len(tweet_texts)}")
    if os.environ.get("BUFFER_ACCESS_TOKEN") and os.environ.get("BUFFER_CHANNEL_ID"):
        pushed = 0
        for tweet in tweet_texts:
            ok = push_tweet_to_buffer(tweet)
            tweet_push_results.append(ok)
            if ok:
                pushed += 1
        log.info(f"Tweets pushed to Buffer: {pushed}/{len(tweet_texts)}")
    else:
        tweet_push_results = [False] * len(tweet_texts)
        log.info("BUFFER_CHANNEL_ID not set — tweets written but not pushed to Buffer")
elif not _enabled("TWITTER_ENABLED"):
    log.info("TWITTER_ENABLED=false — skipping tweets")
else:
    log.info("No stories — skipping tweets")

# ─── Step 4b: Generate and push LinkedIn post(s) ─────────────────────────────

log.info("=== Step 4b: Generating LinkedIn post(s) from top stories ===")
linkedin_results = []
try:
    from linkedin_post import generate_and_push_linkedin_posts
    linkedin_results = generate_and_push_linkedin_posts(stories, owned_source_labels=owned_source_labels)
    for lr in linkedin_results:
        status = "✓ pushed" if lr["linkedin_pushed"] else "✗ draft saved"
        story = lr.get('story', {})
        log.info(
            f"LinkedIn [{status}] grade={story.get('grade')} | "
            f"{story.get('subject', '')[:60]}"
        )
        if not lr["linkedin_pushed"] and _enabled("LINKEDIN_ENABLED") and os.environ.get("LINKEDIN_CHANNEL_ID"):
            notify_sophie("Buffer push failed (LinkedIn). Post saved to linkedin_drafts.txt.")
except Exception as e:
    log.error(f"LinkedIn post step failed: {e}", exc_info=True)
    notify_sophie(f"LinkedIn post step crashed: {str(e)[:120]}")

# ─── Step 4c: Generate Instagram image + caption ─────────────────────────────

log.info("=== Step 4c: Generating Instagram image and caption ===")
instagram_result = None
try:
    from instagram_post import generate_and_push_instagram
    instagram_result = generate_and_push_instagram(stories, owned_source_labels=owned_source_labels)
    if instagram_result:
        status = "✓ pushed" if instagram_result["instagram_pushed"] else "(not pushed)"
        log.info(
            f"Instagram [{status}] image={instagram_result['image_url'][:60]}… | "
            f"caption={instagram_result['caption'][:60]}…"
        )
        if instagram_result.get("instagram_enabled") and not instagram_result["instagram_pushed"]:
            notify_sophie("Buffer push failed (Instagram). Check instagram_drafts.txt.")
except Exception as e:
    log.error(f"Instagram step failed: {e}", exc_info=True)
    notify_sophie(f"Instagram step crashed: {str(e)[:120]}")

# ─── Step 5: Render and send email ───────────────────────────────────────────

from email_renderer import render_briefing_email, send_briefing_email

email_subject_prefix = os.environ.get("EMAIL_SUBJECT_PREFIX", "Daily Newsletter Briefing")

tweets_dict = {
    "tweet1":        tweet_texts[0] if len(tweet_texts) > 0 else "",
    "tweet1_pushed": tweet_push_results[0] if len(tweet_push_results) > 0 else False,
    "tweet2":        tweet_texts[1] if len(tweet_texts) > 1 else "",
    "tweet2_pushed": tweet_push_results[1] if len(tweet_push_results) > 1 else False,
}

linkedin_posts = [
    {
        "sub_category":    lr["sub_category"],
        "stories_used":    len(lr["selected_stories"]),
        "top_grade":       max((s.get("grade", 0) for s in lr["selected_stories"]), default=0),
        "linkedin_post":   lr["linkedin_post"],
        "linkedin_pushed": lr["linkedin_pushed"],
    }
    for lr in linkedin_results
]

try:
    subject, body_html = render_briefing_email(
        today_date=today_et,
        email_subject_prefix=email_subject_prefix,
        stories=stories,
        emails=[{"from": e["from"], "subject": e["subject"]} for e in emails],
        total_fetched=total_fetched,
        total_extracted=len(all_extracted_stories),
        total_included=len(stories),
        tweets=tweets_dict,
        linkedin_posts=linkedin_posts,
        instagram=instagram_result or {},
    )
    if _enabled("EMAIL_ENABLED"):
        result = send_briefing_email(subject, body_html)
        log.info("=== Email sent successfully ===")
        log.info(f"Result: {result}")
    else:
        log.info("EMAIL_ENABLED=false — briefing email rendered but not sent")
except Exception as e:
    log.error(f"Email step failed: {e}", exc_info=True)
    sys.exit(1)
