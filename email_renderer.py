"""
email_renderer.py — Deterministic HTML email renderer for the daily briefing.

Replaces the CrewAI email-composer agent so every field is guaranteed to appear.
"""

import html as _html
import json
import os
import smtplib
import email.mime.multipart
import email.mime.text

from config import get_categories, get_subcategories


def _e(text) -> str:
    """HTML-escape a value; return '' if None/empty."""
    return _html.escape(str(text)) if text else ""


def _category_label(code: str) -> str:
    """Turn '1A' into 'AI & Machine Learning — New Products & Launches'."""
    cats = get_categories()
    subcats = get_subcategories()
    if len(code) >= 2:
        parent = cats.get(code[0], code[0])
        sub = subcats.get(code[1:], code[1:])
        return f"{parent} — {sub}"
    return code


def render_briefing_email(
    today_date: str,
    email_subject_prefix: str,
    stories: list,
    emails: list,
    total_fetched: int,
    total_extracted: int,
    total_included: int,
    tweets: dict,          # {tweet1, tweet1_pushed, tweet2, tweet2_pushed}
    linkedin_posts: list,  # list of linkedin result dicts
    instagram: dict,       # instagram result dict (may be empty)
) -> tuple[str, str]:
    """
    Returns (subject, body_html).
    """
    subject = f"{email_subject_prefix} - {today_date}"

    parts = []
    p = parts.append

    p(f'<h1 style="font-family:sans-serif">{_e(email_subject_prefix)} — {_e(today_date)} ET</h1>')

    # ── Exec summary ──────────────────────────────────────────────────────────
    p('<h2 style="font-family:sans-serif">Exec Summary</h2><ul style="font-family:sans-serif">')
    if stories:
        for s in stories:
            title = _e(s.get("title") or s.get("subject") or "Untitled")
            grade = s.get("grade", "?")
            cat = _e(s.get("category", ""))
            p(f'<li><strong>[{cat}]</strong> {title} — Grade {grade}/10</li>')
    else:
        p("<li>No stories qualified today.</li>")
    p("</ul><hr>")

    # ── Stories ───────────────────────────────────────────────────────────────
    if stories:
        for s in stories:
            cat_code = s.get("category", "")
            cat_label = _category_label(cat_code)
            grade = s.get("grade", "?")
            summary = _e(s.get("summary", ""))
            url = s.get("source", "")
            p(f'<h3 style="font-family:sans-serif">{_e(cat_code)} — {_e(cat_label)}</h3>')
            p(f'<p style="font-family:sans-serif">{summary}<br>')
            if url:
                p(f'Source: <a href="{_e(url)}">{_e(url)}</a> | ')
            p(f'Grade: <strong>{grade}/10</strong></p><hr>')
    else:
        p('<p style="font-family:sans-serif">No stories qualified today (all graded below 5).</p><hr>')

    # ── LinkedIn ──────────────────────────────────────────────────────────────
    p('<h3 style="font-family:sans-serif">LinkedIn Post</h3>')
    if linkedin_posts:
        for lp in linkedin_posts:
            sub_cat = _e(lp.get("sub_category", ""))
            top_grade = lp.get("top_grade", "?")
            stories_used = lp.get("stories_used", 0)
            noun = "story" if stories_used == 1 else "stories"
            pushed = lp.get("linkedin_pushed", False)
            status = "✓ queued in Buffer" if pushed else "(Buffer push skipped)"
            post_text = _e(lp.get("linkedin_post", ""))
            p(f'<p style="font-family:sans-serif"><strong>'
              f'Sub-category {sub_cat} — Top grade {top_grade}/10 — '
              f'{stories_used} {noun} used — {status}'
              f'</strong></p>')
            p(f'<pre style="white-space:pre-wrap;font-size:13px;font-family:sans-serif">{post_text}</pre>')
    else:
        p('<p style="font-family:sans-serif">No LinkedIn post generated today.</p>')
    p("<hr>")

    # ── Instagram ─────────────────────────────────────────────────────────────
    p('<h3 style="font-family:sans-serif">Instagram</h3>')
    if instagram and instagram.get("image_url"):
        pushed = instagram.get("instagram_pushed", False)
        enabled = instagram.get("instagram_enabled", False)
        if pushed:
            ig_status = "✓ queued in Buffer"
        elif enabled:
            ig_status = "✗ push failed"
        else:
            ig_status = "(push disabled — INSTAGRAM_ENABLED not set)"
        image_url = _e(instagram.get("image_url", ""))
        caption = _e(instagram.get("caption", ""))
        content_type = _e(instagram.get("content_type", ""))
        direction = _e(instagram.get("direction", ""))
        dalle_prompt = _e(instagram.get("dalle_prompt", ""))
        p(f'<p style="font-family:sans-serif"><strong>{ig_status}</strong></p>')
        p(f'<p><img src="{image_url}" style="max-width:400px;border-radius:8px"></p>')
        p(f'<pre style="white-space:pre-wrap;font-size:13px;font-family:sans-serif">{caption}</pre>')
        if content_type or direction:
            p(f'<p style="color:#888;font-size:11px">Format: {content_type} — {direction}</p>')
        if dalle_prompt:
            p(f'<p style="color:#888;font-size:11px">DALL-E prompt: {dalle_prompt}</p>')
    else:
        p('<p style="font-family:sans-serif">No Instagram post generated today.</p>')
    p("<hr>")

    # ── Tweets ────────────────────────────────────────────────────────────────
    p('<h3 style="font-family:sans-serif">Tweets</h3><ul style="font-family:sans-serif">')
    for key in ("tweet1", "tweet2"):
        text = tweets.get(key, "")
        if text:
            pushed = tweets.get(f"{key}_pushed", False)
            status = "✓ queued in Buffer" if pushed else "(Buffer push skipped)"
            p(f'<li>{_e(text)} <em>{status}</em></li>')
    p("</ul><hr>")

    # ── Sources ───────────────────────────────────────────────────────────────
    p('<h3 style="font-family:sans-serif">Sources</h3><ul style="font-family:sans-serif">')
    for e in emails:
        sender = _e(e.get("from", ""))
        subj = _e(e.get("subject", ""))
        p(f'<li>{sender} — {subj}</li>')
    p("</ul>")
    p(f'<p style="font-family:sans-serif;color:#666;font-size:13px">'
      f'{total_fetched} emails reviewed | {total_extracted} stories extracted | '
      f'{total_included} stories included (grade≥5)</p>')

    body_html = "\n".join(parts)
    return subject, body_html


def send_briefing_email(subject: str, body_html: str) -> str:
    """Send the rendered email via SMTP. Returns status string."""
    import logging
    log = logging.getLogger(__name__)
    try:
        sender_email    = os.environ["SENDER_EMAIL"]
        sender_password = os.environ["SENDER_APP_PASSWORD"]
        smtp_server     = os.environ.get("SENDER_SMTP_SERVER", "smtp.zoho.com")
        smtp_port       = int(os.environ.get("SENDER_SMTP_PORT", "465"))
        recipients      = [r.strip() for r in os.environ["RECIPIENT_EMAIL"].split(",") if r.strip()]

        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender_email
        msg["To"]      = ", ".join(recipients)
        msg.attach(email.mime.text.MIMEText(body_html, "html"))

        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())

        log.info("Briefing email sent to %s", recipients)
        return "Email sent successfully."
    except Exception as exc:
        log.error("Email send error: %s", exc)
        return f"Email send error: {exc}"
