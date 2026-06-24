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
from collections import defaultdict
from datetime import datetime, timezone

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

    # Step 1 — filter: grade 9-10, any configured category
    eligible = [
        s for s in stories
        if int(s.get("grade", 0)) >= 9
        and str(s.get("category", "")).strip()[:1] in valid_cat_keys
    ]

    if not eligible:
        log.info(
            "LinkedIn selection: 0 eligible stories "
            "(need grade>=9, any category) — skipping today."
        )
        return []

    log.info(
        f"LinkedIn selection: {len(eligible)} eligible stories "
        f"(grade>=9, any category) out of {len(stories)} total."
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


# ─── Post writer ──────────────────────────────────────────────────────────────

_DEFAULT_GUIDELINES = """\
LINKEDIN POST GUIDELINES (strict — not suggestions):
- This is ONE post that synthesises ALL stories into a single point of view.
  Do NOT write a bullet-list of summaries. Find the angle that connects them.
- Open with a specific observation or counter-intuitive claim — never a question.
- State a position, even a minority one, and defend it briefly.
- Short sentences. One idea per sentence. Paragraph breaks generously.
- Maximum 200 words total (longer is allowed only when 3+ stories require it,
  hard cap 280 words).
- If source URLs are provided: include each one in the text where relevant,
  formatted as "👉 [URL]". Do NOT add a separate "Read more" section — weave
  the URLs naturally or list them after the closing line.
- Hashtags at the very end, maximum 4–5 relevant ones.
- No first-person — write as an observer sharing insights.
- Tone: professional, forward-looking, provocative when the material invites it.
- Simple wording — not a native English speaker audience.
- Match the language of the source stories.
- FORBIDDEN words/phrases: "game-changer", "disruptive", "revolutionary",
  "leverage" (as verb), "ecosystem", "excited to share", "thrilled to announce",
  "in today's rapidly evolving landscape", rhetorical engagement-bait questions.
- NEVER use emojis except 👉 before source URLs.\
"""


def _build_stories_block(stories: list[dict]) -> str:
    """Format the selected stories for injection into the prompt."""
    lines = []
    for i, s in enumerate(stories, 1):
        lines.append(f"STORY {i}:")
        lines.append(f"  Sub-category : {s.get('category', '')}")
        lines.append(f"  Grade        : {s.get('grade', '')}/10")
        lines.append(f"  Subject      : {s.get('subject', '')}")
        lines.append(f"  Summary      : {s.get('summary', '')}")
        url = s.get("source", "").strip()
        if url:
            lines.append(f"  Source URL   : {url}")
        lines.append("")
    return "\n".join(lines)


def write_linkedin_post(
    stories: list[dict],
    author_context: str = "",
    owned_source_labels: set | None = None,
) -> dict:
    """
    Write ONE LinkedIn post + tweet covering all selected stories.
    Uses Claude Sonnet if ANTHROPIC_API_KEY is set, otherwise falls back to GPT-4o.
    If any selected story comes from an owned source, switches to first-person
    promotional voice.

    Args:
        stories: list of story dicts (already selected by select_stories())
        author_context: concatenated markdown from the context/ dir
        owned_source_labels: set of source labels that belong to the author

    Returns:
        dict with keys: linkedin_post (str), tweet (str)
    """
    is_owned = any(
        s.get("is_owned_source")
        or (owned_source_labels and s.get("from_newsletter", "") in owned_source_labels)
        for s in stories
    )

    context_block = (
        f"ABOUT THE AUTHOR (voice, background, companies, positions):\n{author_context}\n\n---\n\n"
        if author_context
        else ""
    )

    n = len(stories)
    stories_block = _build_stories_block(stories)

    if is_owned:
        voice_instruction = (
            f"IMPORTANT — OWNED CONTENT MODE:\n"
            f"The stor{'y' if n == 1 else 'ies'} below {'is' if n == 1 else 'are'} "
            f"the author's OWN production (book, podcast, article, video, etc.).\n"
            f"Write in FIRST PERSON ('I', 'my', 'me'). "
            f"The goal is to promote the author's work warmly and personally — "
            f"not as a news summary, but as an invitation to engage with their content.\n"
            f"Keep the author's voice (see guidelines), stay personal and embodied, "
            f"and avoid aggressive marketing language.\n\n"
        )
    else:
        voice_instruction = ""

    prompt = (
        f"You are writing a LinkedIn post on behalf of the author described in the context below.\n\n"
        f"{context_block}"
        f"{voice_instruction}"
        f"GUIDELINES:\n{_DEFAULT_GUIDELINES}\n\n"
        f"You have {n} stor{'y' if n == 1 else 'ies'} from the same news sub-category "
        f"to work with. Your task is to write ONE LinkedIn post that:\n"
        f"  - Finds the thread connecting {'it' if n == 1 else 'them'}\n"
        f"  - Builds a coherent point of view illustrated by the "
        f"{'story' if n == 1 else 'stories'}\n"
        f"  - Does NOT read as a list of news items\n\n"
        f"{'STORY:' if n == 1 else 'STORIES:'}\n{stories_block}\n"
        f"INSTRUCTIONS:\n"
        f"1. Write the LinkedIn post following the guidelines exactly.\n"
        f"2. Write a companion tweet: max 140 chars, "
        f"{'first person, personal and inviting' if is_owned else 'no hashtags, punchy'}, "
        f"include one URL if available.\n\n"
        f"Return ONLY a valid JSON object with exactly two keys:\n"
        f'  "linkedin_post": string\n'
        f'  "tweet": string (max 140 chars, no hashtags)\n'
        f"No preamble, no explanation, no markdown fences. Just the JSON object."
    )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    use_anthropic = bool(anthropic_key)

    try:
        if use_anthropic:
            from anthropic import Anthropic
            client = Anthropic(api_key=anthropic_key)
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                timeout=40.0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            log.info("LinkedIn post written via Claude Sonnet")
        else:
            import openai
            client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.choices[0].message.content.strip()
            log.info("LinkedIn post written via GPT-4o (ANTHROPIC_API_KEY not set)")

        # Strip markdown fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        # Find JSON object boundaries
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]

        result = json.loads(text)

        # Handle occasional double-encoded JSON
        lp = result.get("linkedin_post", "")
        if isinstance(lp, str) and lp.strip().startswith("{"):
            try:
                inner = json.loads(lp)
                if "linkedin_post" in inner:
                    result = inner
            except Exception:
                pass

        result.setdefault("linkedin_post", text)
        result.setdefault("tweet", "")
        return result

    except Exception as e:
        log.error(f"LinkedIn post generation failed: {e}", exc_info=True)
        return {"linkedin_post": "", "tweet": ""}


# ─── Buffer push ──────────────────────────────────────────────────────────────

def _buffer_push(text: str, channel_id: str, label: str = "post") -> bool:
    """Push text to a Buffer channel. Delegates to shared implementation."""
    token      = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    draft_path = os.path.join(os.path.dirname(__file__), "linkedin_drafts.txt")
    return _push_to_buffer_shared(text, channel_id, token, label=label, draft_path=draft_path)


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
    linkedin_post = post_data.get("linkedin_post", "").strip()
    tweet         = post_data.get("tweet", "").strip()

    if not linkedin_post:
        log.error("LinkedIn post generation returned empty content — aborting push.")
        return []

    log.info(f"LinkedIn post written ({len(linkedin_post)} chars):\n{linkedin_post[:300]}…")

    # ── 4. Push to Buffer ─────────────────────────────────────────────────────
    linkedin_pushed = False
    if linkedin_channel_id:
        linkedin_pushed = _buffer_push(linkedin_post, linkedin_channel_id, label="LinkedIn")
    else:
        log.warning("Skipping LinkedIn push — LINKEDIN_CHANNEL_ID not set.")

    tweet_pushed = False
    if tweet and twitter_channel_id:
        if len(tweet) > 140:
            log.warning(f"Tweet too long ({len(tweet)} chars) — truncating.")
            tweet = tweet[:137] + "…"
        tweet_pushed = _buffer_push(tweet, twitter_channel_id, label="tweet")
    elif tweet and not twitter_channel_id:
        log.info("Tweet written but TWITTER_CHANNEL_ID not set — skipping tweet push.")

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
        "linkedin_post":     linkedin_post,
        "tweet":             tweet,
        "linkedin_pushed":   linkedin_pushed,
        "tweet_pushed":      tweet_pushed,
    }]
