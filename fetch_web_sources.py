"""
fetch_web_sources.py — Fetch stories from web sources listed in sources.md.

Supports:
  - RSS / Atom feeds  (auto-detected by Content-Type or feed structure)
  - Plain web pages   (fetched and HTML-stripped)

Returns a list of dicts in the same shape as email dicts used by run_crew.py:
  { thread_id, subject, from, body_text }

so GPT-4o sees a uniform input regardless of source type.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Alabama-NewsBot/1.0; "
        "+https://github.com/pbucquet/Alabama)"
    )
}
_TIMEOUT = 15
_MAX_BODY_CHARS = 5000
_MAX_RSS_ITEMS = 20


# ─── sources.md parser ────────────────────────────────────────────────────────

_OWNED_SECTION_MARKERS = (
    "owned content",
    "sources personnelles",
    "personal",
)

def load_sources(path: str | None = None) -> list[tuple[str, str, bool]]:
    """
    Parse sources.md and return a list of (url, label, is_owned) tuples.

    is_owned is True for URLs found under a section whose heading contains
    "owned content", "sources personnelles", or "personal" (case-insensitive).
    Lines starting with # or <!-- are skipped.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.md")

    if not os.path.isfile(path):
        log.info(f"No sources.md found at '{path}' — skipping web sources.")
        return []

    sources: list[tuple[str, str, bool]] = []
    in_owned_section = False

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("<!--") or line.startswith("-->"):
                continue
            # Section headings (## or ###) — detect owned section
            if line.startswith("##"):
                heading_lower = line.lstrip("#").strip().lower()
                in_owned_section = any(m in heading_lower for m in _OWNED_SECTION_MARKERS)
                continue
            # Skip comment lines
            if line.startswith("#"):
                continue
            # Must contain a URL
            url_match = re.search(r'https?://\S+', line)
            if not url_match:
                continue
            url = url_match.group(0).rstrip(")")
            label_match = re.search(r'(?:—|-)\s+(.+)$', line[url_match.end():])
            label = label_match.group(1).strip() if label_match else url
            sources.append((url, label, in_owned_section))

    owned = sum(1 for _, _, o in sources if o)
    log.info(f"Loaded {len(sources)} web source(s) from sources.md ({owned} owned)")
    return sources


def get_owned_urls(path: str | None = None) -> set[str]:
    """Return the set of URLs marked as owned content in sources.md."""
    return {url for url, _, is_owned in load_sources(path) if is_owned}


# ─── RSS / Atom fetcher ───────────────────────────────────────────────────────

_RSS_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()


def _parse_rss(xml_text: str, label: str, since: datetime | None) -> list[dict]:
    """Parse RSS 2.0 or Atom feed. Returns list of email-shaped dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning(f"RSS parse error for '{label}': {e}")
        return []

    items: list[dict] = []

    # Atom feed
    if root.tag == "{http://www.w3.org/2005/Atom}feed" or root.tag == "feed":
        ns = "http://www.w3.org/2005/Atom"
        entries = root.findall(f"{{{ns}}}entry") or root.findall("entry")
        for entry in entries[:_MAX_RSS_ITEMS]:
            title = (entry.findtext(f"{{{ns}}}title") or entry.findtext("title") or "").strip()
            link_el = entry.find(f"{{{ns}}}link") or entry.find("link")
            link = ""
            if link_el is not None:
                link = link_el.get("href", "") or link_el.text or ""
            summary = (
                entry.findtext(f"{{{ns}}}summary")
                or entry.findtext(f"{{{ns}}}content")
                or entry.findtext("summary")
                or entry.findtext("content")
                or ""
            )
            pub = (
                entry.findtext(f"{{{ns}}}updated")
                or entry.findtext(f"{{{ns}}}published")
                or entry.findtext("updated")
                or entry.findtext("published")
                or ""
            )
            items.append(_make_item(title, link, summary, pub, label, since))

    else:
        # RSS 2.0 — find <channel><item> or <item> directly
        channel = root.find("channel") or root
        for item in (channel.findall("item") or [])[:_MAX_RSS_ITEMS]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            summary = (
                item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
                or item.findtext("description")
                or ""
            )
            pub = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or ""
            items.append(_make_item(title, link, summary, pub, label, since))

    return [i for i in items if i is not None]


def _make_item(
    title: str,
    link: str,
    body: str,
    pub_date: str,
    label: str,
    since: datetime | None,
) -> dict | None:
    """Build an email-shaped dict. Returns None if item is too old."""
    if since and pub_date:
        try:
            # Try ISO format first, then RFC 2822
            try:
                pub_dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
            except ValueError:
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub_date)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt < since:
                return None
        except Exception:
            pass  # Can't parse date — include item anyway

    clean_body = _strip_html(body)
    body_text = f"Title: {title}\nURL: {link}\n\n{clean_body}"
    return {
        "thread_id": f"web:{link or title[:60]}",
        "subject": title[:120] if title else "(no title)",
        "from": label,
        "body_text": body_text[:_MAX_BODY_CHARS],
        "is_owned_source": False,  # set by caller
    }


# ─── Plain HTML fetcher ───────────────────────────────────────────────────────

def _fetch_html(url: str, label: str) -> dict | None:
    """Fetch a plain web page and return an email-shaped dict."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        text = _strip_html(resp.text)
        # Try to extract a title from <title>...</title>
        title_match = re.search(r'<title[^>]*>(.*?)</title>', resp.text, re.IGNORECASE | re.DOTALL)
        title = _strip_html(title_match.group(1)) if title_match else label
        return {
            "thread_id": f"web:{url}",
            "subject": title[:120],
            "from": label,
            "body_text": text[:_MAX_BODY_CHARS],
            "is_owned_source": False,  # set by caller
        }
    except Exception as e:
        log.warning(f"Failed to fetch '{url}': {e}")
        return None


# ─── Main entry point ─────────────────────────────────────────────────────────

def fetch_web_sources(
    sources_path: str | None = None,
    since: datetime | None = None,
) -> list[dict]:
    """
    Load sources.md, fetch each URL, and return a list of email-shaped dicts.

    Args:
        sources_path: path to sources.md (default: ./sources.md next to this file)
        since: only return RSS items published after this datetime (UTC-aware).
               Plain HTML pages are always returned (no publish date available).
    """
    sources = load_sources(sources_path)
    if not sources:
        return []

    results: list[dict] = []
    for url, label, is_owned in sources:
        log.info(f"Fetching web source: {label} ({url}){' [OWNED]' if is_owned else ''}")
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            content_type = resp.headers.get("Content-Type", "")

            is_feed = (
                "xml" in content_type
                or "rss" in content_type
                or "atom" in content_type
                or resp.text.lstrip().startswith("<?xml")
                or "<rss" in resp.text[:500]
                or "<feed" in resp.text[:500]
            )

            if is_feed:
                items = _parse_rss(resp.text, label, since)
                for item in items:
                    item["is_owned_source"] = is_owned
                log.info(f"  RSS: {len(items)} item(s) from '{label}'")
                results.extend(items)
            else:
                item = _fetch_html(url, label)
                if item:
                    item["is_owned_source"] = is_owned
                    log.info(f"  HTML: 1 page fetched from '{label}'")
                    results.append(item)

        except Exception as e:
            log.warning(f"Error fetching '{label}' ({url}): {e}")

    log.info(f"Web sources total: {len(results)} item(s) from {len(sources)} source(s)")
    return results
