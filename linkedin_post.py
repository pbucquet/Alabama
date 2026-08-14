"""
linkedin_post.py — LinkedIn post generator for the Daily Newsletter Briefing crew.

SELECTION LOGIC:
  1. Filter: only stories with grade 9 or 10, from ANY category (1, 2, 3, or 4).
  2. Group filtered stories by sub-category (e.g. 1A, 2D, 3B, 4D).
  3. Select the sub-category with the most qualifying stories.
  4. Tie-breaking (equal count):
       a. Rarest category wins: 4 (Consulting) > 3 (Fintech) > 2 (Blockchain) > 1 (AI).
          Rationale: category 4 stories are rare — when one reaches grade 9+, prioritise it.
       b. Within the same parent category: pick randomly.
  5. If no story qualifies → skip LinkedIn entirely that day.
  6. All stories in the winning sub-category are passed to the writer (no cap).

POST WRITING:
  All selected stories are combined into ONE LinkedIn post that builds a
  coherent point of view illustrated by the stories — not a list of summaries.

Called from run_crew.py after tweets (Step 4), before the briefing email (Step 5).

ENV VARS required:
  ANTHROPIC_API_KEY     — Claude Sonnet for post writing (optional; falls back to GPT-4o)
  BUFFER_ACCESS_TOKEN   — Buffer OAuth token
  LINKEDIN_CHANNEL_ID   — Buffer channel ID for LinkedIn

ENV VARS optional:
  TWITTER_CHANNEL_ID    — Buffer channel ID for Twitter (tweet also pushed if set)
  CONTEXT_DIR           — Path to the author context/ dir (default: ./context inside this repo)
  STORY_CATEGORIES      — Pipe-separated key:label pairs for parent categories (see config.py)
  STORY_SUBCATEGORIES   — Pipe-separated key:label pairs for sub-categories (see config.py)
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import importlib.util as _ilu
import requests as _requests
from collections import defaultdict
from datetime import datetime, timezone
from alabama_core.write_post import write_linkedin_post as _core_write_linkedin_post

_bt = _ilu.spec_from_file_location("buffer_tool", os.path.join(os.path.dirname(__file__), "..", "shared", "tools", "buffer_tool.py"))
_bm = _ilu.module_from_spec(_bt); _bt.loader.exec_module(_bm)
_push_to_buffer_shared = _bm.push_to_buffer

log = logging.getLogger(__name__)


# ─── Story selection ──────────────────────────────────────────────────────────

def select_stories(stories: list[dict]) -> list[dict]:
    """
    Apply the selection logic and return the stories to write a post about.
    Returns an empty list when no post should be published today.

    Selection rules:
    - Grade 9 or 10 only
    - All categories eligible (1=AI, 2=Blockchain/Crypto, 3=Fintech/Insurtech, 4=Consulting)
    - Group filtered stories by sub-category (e.g. "1A", "3D", "4B")
    - Pick the sub-category with the most qualifying stories
    - Tie-break: rarest category wins (4 > 3 > 2 > 1), then random within same parent
    - Return ALL stories in the winning sub-category
    """
    from config import get_categories, get_priority_order
    valid_cat_keys = set(get_categories().keys())
    priority_order = get_priority_order()

    # Step 1 — filter: grade 9-10, any configured category, source URL required (hard rule)
    def _has_source(s: dict) -> bool:
        src = str(s.get("source", "")).strip().lower()
        return bool(src) and src not in ("not available", "n/a", "none", "null")

    min_grade = int(os.environ.get("MIN_LINKEDIN_GRADE", "9"))
    eligible = [
        s for s in stories
        if int(s.get("grade", 0)) >= min_grade
        and str(s.get("category", "")).strip()[:1] in valid_cat_keys
        and _has_source(s)
    ]

    no_source_count = sum(
        1 for s in stories
        if int(s.get("grade", 0)) >= 9
        and not _has_source(s)
    )
    if no_source_count:
        log.info(
            f"LinkedIn selection: {no_source_count} grade-9/10 story/stories excluded "
            "— no source URL (hard rule: no source, no LinkedIn post)."
        )

    if not eligible:
        log.info(
            f"LinkedIn selection: 0 eligible stories "
            f"(need grade>={min_grade}, any category) — skipping today."
        )
        return []

    log.info(
        f"LinkedIn selection: {len(eligible)} eligible stories "
        f"(grade>={min_grade}, any category) out of {len(stories)} total."
    )

    # Step 2 — group by sub-category
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in eligible:
        subcat = str(s.get("category", "")).strip().upper()
        groups[subcat].append(s)

    # Step 3 — find the sub-category/ies with the most stories
    max_count = max(len(v) for v in groups.values())
    winners = [k for k, v in groups.items() if len(v) == max_count]

    log.info(
        f"LinkedIn selection: sub-category counts = "
        + ", ".join(f"{k}:{len(groups[k])}" for k in sorted(groups))
        + f" | max={max_count} | tied winners={winners}"
    )

    # Step 4 — tie-breaking: rarest category wins (last in STORY_CATEGORIES = highest priority)
    if len(winners) == 1:
        chosen_subcat = winners[0]
    else:
        for priority_cat in priority_order:
            priority_winners = [w for w in winners if w.startswith(priority_cat)]
            if priority_winners:
                chosen_subcat = random.choice(priority_winners)
                break

    selected = groups[chosen_subcat]
    log.info(
        f"LinkedIn selection: chose sub-category '{chosen_subcat}' "
        f"with {len(selected)} story/stories — "
        + ", ".join(
            f"grade={s.get('grade')} '{s.get('subject','')[:50]}'"
            for s in selected
        )
    )
    return selected


# ─── Context loader ───────────────────────────────────────────────────────────

def _load_author_context() -> str:
    """
    Load author voice/style/position markdown files from the context/ dir.
    Returns concatenated markdown, or empty string if the dir is not found.
    """
    default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context")
    context_dir = os.environ.get("CONTEXT_DIR", default_dir)
    if not os.path.isdir(context_dir):
        log.warning(
            f"CONTEXT_DIR not found at '{context_dir}'. "
            "LinkedIn posts will use built-in guidelines only. "
            "Populate the context/ folder or set CONTEXT_DIR in your .env."
        )
        return ""

    chunks: list[str] = []
    for root, _, files in os.walk(context_dir):
        for fname in sorted(files):
            if fname.endswith(".md"):
                path = os.path.join(root, fname)
                with open(path, "r", encoding="utf-8") as f:
                    rel = os.path.relpath(path, context_dir)
                    chunks.append(f"## [{rel}]\n{f.read()}")

    log.info(f"Author context loaded: {len(chunks)} file(s) from '{context_dir}'.")
    return "\n\n---\n\n".join(chunks)


# ─── Post writer (delegates to alabama_core) ─────────────────────────────────

def write_linkedin_post(
    stories: list[dict],
    author_context: str = "",
    owned_source_labels: set | None = None,
) -> dict:
    """Delegates to alabama_core.write_post — Alabama is the source of truth."""
    return _core_write_linkedin_post(
        stories=stories,
        author_context=author_context,
        owned_source_labels=owned_source_labels,
    )


# ─── Buffer push ──────────────────────────────────────────────────────────────

def _buffer_push(text: str, channel_id: str, label: str = "post") -> bool:
    """Push text to a Buffer channel. Delegates to shared implementation."""
    token      = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    draft_path = os.path.join(os.path.dirname(__file__), "linkedin_drafts.txt")
    return _push_to_buffer_shared(text, channel_id, token, label=label, draft_path=draft_path)


# ─── Website webhook ──────────────────────────────────────────────────────────

def _post_to_webhook(webhook_url: str, title: str, summary: str, linkedin_post: str) -> None:
    """POST the LinkedIn post to an external website endpoint. Fire-and-forget."""
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "date":          today_iso,
        "title":         title,
        "summary":       summary,
        "linkedin_post": linkedin_post,
    }
    headers = {"Content-Type": "application/json"}
    secret = os.environ.get("ALABAMA_WEBHOOK_SECRET", "").strip()
    if secret:
        headers["X-Alabama-Webhook-Secret"] = secret
    try:
        resp = _requests.post(webhook_url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        log.info(f"Webhook POST to {webhook_url} succeeded ({resp.status_code}).")
    except Exception as exc:
        log.warning(f"Webhook POST to {webhook_url} failed (non-fatal): {exc}")


# ─── Main entry point ─────────────────────────────────────────────────────────

def generate_and_push_linkedin_posts(stories: list[dict], owned_source_labels: set | None = None) -> list[dict]:
    """
    Full pipeline: select → write → push.

    Args:
        stories: all grade>=5 stories from today's run (pre-filtered by run_crew.py)

    Returns:
        list with 0 or 1 result dict:
          {
            selected_stories:  list of story dicts used,
            sub_category:      str  (e.g. "1A"),
            linkedin_post:     str,
            tweet:             str,
            linkedin_pushed:   bool,
            tweet_pushed:      bool,
          }
        Empty list = no post today.
    """
    linkedin_channel_id = os.environ.get("LINKEDIN_CHANNEL_ID", "")
    twitter_channel_id  = os.environ.get("TWITTER_CHANNEL_ID", "")

    if not linkedin_channel_id:
        log.error(
            "LINKEDIN_CHANNEL_ID not set — LinkedIn posts cannot be pushed. "
            "Add it to your .env file."
        )

    # ── 1. Selection ──────────────────────────────────────────────────────────
    selected = select_stories(stories)
    if not selected:
        return []

    sub_category = str(selected[0].get("category", "")).strip().upper()

    # ── 2. Load author context ────────────────────────────────────────────────
    author_context = _load_author_context()

    # ── 3. Write post ─────────────────────────────────────────────────────────
    log.info(
        f"Writing LinkedIn post for sub-category '{sub_category}' "
        f"({len(selected)} stor{'y' if len(selected)==1 else 'ies'})…"
    )
    post_data     = write_linkedin_post(selected, author_context=author_context, owned_source_labels=owned_source_labels)
    title         = post_data.get("title", "").strip()
    summary       = post_data.get("summary", "").strip()
    linkedin_post = post_data.get("linkedin_post", "").strip()
    tweet         = post_data.get("tweet", "").strip()

    if not linkedin_post:
        log.error("LinkedIn post generation returned empty content — aborting push.")
        return []

    log.info(f"LinkedIn post written ({len(linkedin_post)} chars):\n{linkedin_post[:300]}…")

    # ── 4. Push to Buffer ─────────────────────────────────────────────────────
    linkedin_enabled = os.environ.get("LINKEDIN_ENABLED", "true").strip().lower() in ("true", "1", "yes")
    twitter_enabled  = os.environ.get("TWITTER_ENABLED",  "true").strip().lower() in ("true", "1", "yes")

    linkedin_pushed = False
    if not linkedin_enabled:
        log.info("LINKEDIN_ENABLED=false — skipping LinkedIn Buffer push.")
    elif linkedin_channel_id:
        linkedin_pushed = _buffer_push(linkedin_post, linkedin_channel_id, label="LinkedIn")
    else:
        log.warning("Skipping LinkedIn push — LINKEDIN_CHANNEL_ID not set.")

    tweet_pushed = False
    if not twitter_enabled:
        log.info("TWITTER_ENABLED=false — skipping LinkedIn companion tweet push.")
    elif tweet and twitter_channel_id:
        if len(tweet) > 140:
            log.warning(f"Tweet too long ({len(tweet)} chars) — truncating.")
            tweet = tweet[:137] + "…"
        tweet_pushed = _buffer_push(tweet, twitter_channel_id, label="tweet")
    elif tweet and not twitter_channel_id:
        log.info("Tweet written but TWITTER_CHANNEL_ID not set — skipping tweet push.")

    # ── 5. POST to website webhook(s) (fire-and-forget) ───────────────────────
    for webhook_url in [u.strip() for u in os.environ.get("LINKEDIN_WEBHOOK_URL", "").split(",") if u.strip()]:
        _post_to_webhook(
            webhook_url=webhook_url,
            title=title,
            summary=summary,
            linkedin_post=linkedin_post,
        )

    log.info(
        f"LinkedIn step complete — "
        f"sub-category: {sub_category} | "
        f"stories used: {len(selected)} | "
        f"LinkedIn: {'pushed' if linkedin_pushed else 'FAILED/draft'} | "
        f"tweet: {'pushed' if tweet_pushed else 'skipped/FAILED'}"
    )

    return [{
        "selected_stories":  selected,
        "sub_category":      sub_category,
        "title":             title,
        "summary":           summary,
        "linkedin_post":     linkedin_post,
        "tweet":             tweet,
        "linkedin_pushed":   linkedin_pushed,
        "tweet_pushed":      tweet_pushed,
    }]
