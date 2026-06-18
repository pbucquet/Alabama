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

# ─── Validate environment ─────────────────────────────────────────────────────

REQUIRED_ENV = [
    "OPENAI_API_KEY",
    "AGENTMAIL_API_KEY",
    "SENDER_INBOX_ID",
    "RECIPIENT_EMAIL",
    "SENDER_EMAIL",
    "SENDER_APP_PASSWORD",
    "BUFFER_ACCESS_TOKEN",
    "BUFFER_CHANNEL_ID",
]

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
from agentmail import AgentMail

def strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()

def clean_json(raw: str) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)

client_am = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
inbox_id = os.environ["SENDER_INBOX_ID"]

threads = client_am.inboxes.threads.list(
    inbox_id=inbox_id,
    after=since,
    limit=50,
    include_spam=True,
)
log.info(f"Fetched {len(threads.threads)} email threads from AgentMail")

emails = []
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

total_fetched = len(emails)
log.info(f"Successfully read {total_fetched} emails")

# ─── Step 2b: Fetch web sources ───────────────────────────────────────────────

try:
    from fetch_web_sources import fetch_web_sources
    web_items = fetch_web_sources(since=since)
    if web_items:
        emails.extend(web_items)
        log.info(f"Added {len(web_items)} item(s) from web sources — total inputs: {len(emails)}")
except Exception as e:
    log.warning(f"Web source fetch failed (non-fatal): {e}", exc_info=True)

# ─── Step 3: Extract stories via GPT-4o ──────────────────────────────────────
# GPT-4o returns ALL relevant stories (any grade).
# Grade filtering happens in Python so we can count accurately.

all_extracted_stories = []
stories = []  # grade >= 5

if emails:
    import openai
    oc = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

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

    # Build email block for prompt
    email_text = ""
    for i, em in enumerate(emails):
        email_text += (
            f"EMAIL {i+1}:\n"
            f"From: {em['from'][:60]}\n"
            f"Subject: {em['subject'][:80]}\n"
            f"Body: {em['body_text'][:5000]}\n\n"
        )

    from config import get_categories, get_subcategories, get_all_codes
    _cats = get_categories()
    _subcats = get_subcategories()
    _codes = ",".join(get_all_codes())
    _cat_lines = "\n".join(
        f"      {k} = {v}" for k, v in _cats.items()
    )
    _subcat_lines = "\n".join(
        f"      {k} = {v}" for k, v in _subcats.items()
    )
    _cat_names = ", ".join(_cats.values())

    # NOTE: Grade filter is intentionally removed from prompt.
    # We get all relevant stories and filter in Python for accurate counts.
    prompt = (
        f"You are a market intelligence analyst. Process ALL {len(emails)} emails below.\n"
        f"For each email, determine if it contains business news relevant to:\n"
        f"{_cat_names}.\n\n"
        f"CRITICAL — DECOMPOSE ROUNDUPS: If an email contains a list or roundup section "
        f"(e.g. 'Top 5 AI Tools', 'New & Trending', 'This Week in AI', bullet lists of products/news), "
        f"extract EACH item as a SEPARATE story object. Do not collapse a list into one story.\n\n"
        f"INCLUSION BIAS: When in doubt, include. It is better to extract a grade-3 story "
        f"than to miss a grade-8 story. Only skip items that are clearly irrelevant "
        f"(social notifications, security codes, delivery receipts, pure marketing with zero news value).\n\n"
        f"PAY SPECIAL ATTENTION to:\n"
        f"  - New AI model launches or capability announcements (always extract)\n"
        f"  - New AI tools, products, or developer features (always extract, even if brief)\n"
        f"  - Funding rounds for startups in these categories\n"
        f"  - Enterprise deployments or partnerships\n\n"
        f"If RELEVANT: extract as a story object with these fields:\n"
        f"  - category: one of {_codes}\n"
        f"    Parent categories — assign based on the PRIMARY SUBJECT of the story:\n"
        f"{_cat_lines}\n"
        f"    Sub-categories (letter):\n"
        f"{_subcat_lines}\n"
        f"    IMPORTANT: assign based on the PRIMARY subject of the story — "
        f"the most specific matching category wins.\n"
        f"  - grade: integer 1-10 (business impact + novelty)\n"
        f"  - summary: factual summary, minimum 3 sentences and 60 words, maximum 120 words\n"
        f"  - source: URL from the email if available, else empty string\n"
        f"  - from_newsletter: sender name\n"
        f"  - subject: email subject\n\n"
        f"Skip if source URL already in this list: {existing_sources[:20]}\n"
        f"Include ALL relevant stories regardless of grade — include grades 1 through 10.\n\n"
        f"Return ONLY a valid JSON array. No markdown, no explanation, no preamble.\n\n"
        f"EMAILS:\n{email_text}"
    )

    try:
        resp = oc.chat.completions.create(
            model="gpt-4o",
            max_tokens=12000,  # raised to handle larger email bodies
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        all_extracted_stories = json.loads(raw)

        now_iso = datetime.now(timezone.utc).isoformat()
        for s in all_extracted_stories:
            s["timestamp"] = now_iso

        # Filter by grade in Python — gives accurate counts
        stories = [s for s in all_extracted_stories if int(s.get("grade", 0)) >= 5]

        log.info(
            f"Story extraction complete: "
            f"{total_fetched} emails fetched | "
            f"{len(all_extracted_stories)} stories extracted | "
            f"{len(stories)} stories included (grade>=5)"
        )

    except json.JSONDecodeError as e:
        log.error(f"GPT-4o returned invalid JSON: {e}")
        log.error(f"Raw response (first 500 chars): {raw[:500]}")
        all_extracted_stories = []
        stories = []
    except Exception as e:
        log.error(f"Story extraction failed: {e}", exc_info=True)
        all_extracted_stories = []
        stories = []

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

def push_tweet_to_buffer(tweet_text: str) -> bool:
    """Push a single tweet to Buffer via GraphQL. Returns True on success."""
    import requests as req_lib
    token = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    channel_id = os.environ.get("BUFFER_CHANNEL_ID", "")
    if not token or not channel_id:
        log.error("BUFFER_ACCESS_TOKEN or BUFFER_CHANNEL_ID not set")
        return False
    tweet_escaped = json.dumps(tweet_text)
    mutation = f"""
    mutation CreatePost {{
      createPost(input: {{
        text: {tweet_escaped},
        channelId: "{channel_id}",
        schedulingType: automatic,
        mode: addToQueue
      }}) {{
        ... on PostActionSuccess {{
          post {{ id }}
        }}
        ... on MutationError {{
          message
        }}
      }}
    }}
    """
    try:
        resp = req_lib.post(
            "https://api.buffer.com",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={"query": mutation},
            timeout=15,
        )
        result = resp.json()
        if result.get("errors"):
            raise Exception(json.dumps(result["errors"]))
        post_result = result.get("data", {}).get("createPost", {})
        if post_result.get("post", {}).get("id"):
            log.info(f"Buffer push OK — post ID: {post_result['post']['id']} | {tweet_text}")
            return True
        else:
            raise Exception(post_result.get("message", json.dumps(result)))
    except Exception as e:
        log.error(f"Buffer push failed: {e}")
        draft_path = os.path.join(os.path.dirname(__file__), "tweets_draft.txt")
        with open(draft_path, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} | {tweet_text}\n")
        return False


def write_tweets(stories: list, oc) -> list:
    """Ask GPT-4o to write 2 tweets for the top 2 stories. Returns list of tweet strings."""
    if not stories:
        return []
    top2 = sorted(stories, key=lambda s: int(s.get("grade", 0)), reverse=True)[:2]
    results = []
    for story in top2:
        url = story.get("source", "")
        url_len = len(url) + 1  # +1 for the space before URL
        max_text_len = 140 - url_len if url else 140
        prompt = (
            "Write a single tweet for this story. Rules:\n"
            "- Factual and punchy. No hashtags. No emojis.\n"
            f"- End with the source URL: {url}\n"
            f"- HARD LIMIT: 140 characters TOTAL (URL included). "
            f"The URL alone is {url_len} chars, so your text before it must be "
            f"{max_text_len} chars or fewer.\n"
            "- Count every character carefully before responding.\n"
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
                if len(candidate) <= 140:
                    tweet = candidate
                    log.info(f"Tweet written ({len(tweet)} chars): {tweet}")
                    break
                else:
                    log.warning(f"Tweet too long ({len(candidate)} chars), retrying... (attempt {attempt+1}/5)")
                    prompt += (
                        f"\n\nAttempt {attempt+1} was {len(candidate)} chars — still too long. "
                        f"You MUST stay under 140 chars total. Cut words ruthlessly."
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
if stories:
    tweet_texts = write_tweets(stories, oc)
    pushed = 0
    for tweet in tweet_texts:
        if push_tweet_to_buffer(tweet):
            pushed += 1
    log.info(f"Tweets: {len(tweet_texts)} written, {pushed} pushed to Buffer")
else:
    log.info("No stories — skipping tweets")

# ─── Step 4b: Generate and push LinkedIn post(s) ─────────────────────────────

log.info("=== Step 4b: Generating LinkedIn post(s) from top stories ===")
linkedin_results = []
try:
    from linkedin_post import generate_and_push_linkedin_posts
    linkedin_results = generate_and_push_linkedin_posts(stories)
    for lr in linkedin_results:
        status = "✓ pushed" if lr["linkedin_pushed"] else "✗ draft saved"
        story = lr.get('story', {})
        log.info(
            f"LinkedIn [{status}] grade={story.get('grade')} | "
            f"{story.get('subject', '')[:60]}"
        )
except Exception as e:
    log.error(f"LinkedIn post step failed: {e}", exc_info=True)

# ─── Step 5: Send email via crew ──────────────────────────────────────────────

from crew import build_crew

tweets_json = json.dumps({
    "tweet1": tweet_texts[0] if len(tweet_texts) > 0 else "",
    "tweet2": tweet_texts[1] if len(tweet_texts) > 1 else "",
})

# linkedin_results is a list of 0 or 1 dicts (one post per day)
linkedin_json = json.dumps([
    {
        "sub_category":    lr["sub_category"],
        "stories_used":    len(lr["selected_stories"]),
        "top_grade":       max((s.get("grade", 0) for s in lr["selected_stories"]), default=0),
        "grades":          [s.get("grade") for s in lr["selected_stories"]],
        "subjects":        [s.get("subject", "") for s in lr["selected_stories"]],
        "linkedin_post":   lr["linkedin_post"],
        "linkedin_pushed": lr["linkedin_pushed"],
    }
    for lr in linkedin_results
])

try:
    crew = build_crew()
    result = crew.kickoff(inputs={
        "today_date": today_et,
        "today_iso": today_iso,
        "stories_json": json.dumps(stories),
        "emails_json": json.dumps([
            {"from": e["from"], "subject": e["subject"]} for e in emails
        ]),
        "total_fetched": str(total_fetched),
        "total_extracted": str(len(all_extracted_stories)),
        "total_included": str(len(stories)),
        "tweets_json": tweets_json,
        "linkedin_json": linkedin_json,
    })
    log.info("=== Crew completed successfully ===")
    log.info(f"Result: {str(result)[:300]}")
except Exception as e:
    log.error(f"Crew failed: {e}", exc_info=True)
    sys.exit(1)
