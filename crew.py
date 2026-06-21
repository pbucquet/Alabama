"""
crew.py — Newsletter Briefing Crew
Tweets are written and pushed in run_crew.py (pure Python).
This crew handles only the email composition and sending.
"""

import os
from crewai import Agent, Task, Crew, Process, LLM
from tools import SendEmailTool

# ─── LLM ──────────────────────────────────────────────────────────────────────

gpt4o = LLM(
    model="gpt-4o",
    api_key=os.environ.get("OPENAI_API_KEY"),
)

# ─── Tools ────────────────────────────────────────────────────────────────────

send_email = SendEmailTool()

# ─── Agent ────────────────────────────────────────────────────────────────────

email_composer = Agent(
    role="Email Composer",
    goal="Compose and send the daily newsletter briefing email.",
    backstory=(
        "You are a sharp business communicator who writes clean, well-structured "
        "HTML briefing emails for a professional AI/tech/finance audience."
    ),
    llm=gpt4o,
    tools=[send_email],
    verbose=True,
    max_iter=3,
)

# ─── Task ─────────────────────────────────────────────────────────────────────

task_email = Task(
    description=(
        "Compose and send the daily briefing email.\n\n"
        "Date: {today_date}\n"
        "Stories: {stories_json}\n"
        "Source emails: {emails_json}\n"
        "Counts: {total_fetched} fetched | {total_extracted} extracted | {total_included} included (grade>=5)\n"
        "Tweets: {tweets_json}\n"
        "LinkedIn posts: {linkedin_json}\n"
        "Instagram: {instagram_json}\n\n"
        "Send via send_email tool with:\n"
        "  subject: '{email_subject_prefix} - {today_iso}'\n"
        "  body_html: full HTML email with this structure:\n\n"
        "    <h1>{email_subject_prefix} -- {today_date} ET</h1>\n"
        "    <h2>Exec Summary</h2>\n"
        "    <ul>[one bullet per story, concise]</ul>\n"
        "    <hr>\n"
        "    [For each story:]\n"
        "    <h3>[category code] -- [category name]</h3>\n"
        "    <p>[summary]<br>Source: <a href=\"[url]\">[url]</a> | Grade: X/10</p>\n"
        "    <hr>\n"
        "    <h3>LinkedIn Post</h3>\n"
        "    [For each entry in linkedin_json, render:]\n"
        "    <p><strong>Sub-category [sub_category] — Top grade [top_grade]/10 — [stories_used] stor(y/ies) used"
        " — [if linkedin_pushed: '✓ queued in Buffer' else '(Buffer push skipped)']</strong></p>\n"
        "    <pre style=\"white-space:pre-wrap;font-size:13px\">[linkedin_post]</pre>\n"
        "    <hr>\n"
        "    <h3>Instagram</h3>\n"
        "    [If instagram_json is not empty, render:]\n"
        "    <p><strong>[if instagram_pushed: '✓ queued in Buffer' elif instagram_enabled: '✗ push failed' else '(push disabled — INSTAGRAM_ENABLED not set)']</strong></p>\n"
        "    <p><img src=\"[image_url]\" style=\"max-width:400px;border-radius:8px\"> </p>\n"
        "    <pre style=\"white-space:pre-wrap;font-size:13px\">[caption]</pre>\n"
        "    <p style=\"color:#888;font-size:11px\">DALL-E prompt: [dalle_prompt]</p>\n"
        "    <hr>\n"
        "    <h3>Tweets</h3>\n"
        "    <ul>\n"
        "      [one <li> per non-empty tweet from tweets_json; note if pushed to Buffer or not]\n"
        "    </ul>\n"
        "    <hr>\n"
        "    <h3>Sources</h3>\n"
        "    <ul>[one <li> per email: sender — subject]</ul>\n"
        "    <p>{total_fetched} emails reviewed | {total_extracted} stories extracted | "
        "{total_included} stories included (grade>=5)</p>\n\n"
        "If stories_json is empty, send a brief email saying no stories qualified today."
    ),
    expected_output="Confirmation that the email was sent successfully.",
    agent=email_composer,
)

# ─── Crew ─────────────────────────────────────────────────────────────────────

def build_crew() -> Crew:
    return Crew(
        agents=[email_composer],
        tasks=[task_email],
        process=Process.sequential,
        verbose=True,
    )
