"""
tools.py — AgentMail read/send wrappers + Buffer tweet tool for CrewAI
All attribute names verified against live AgentMail SDK responses.
"""

import os
import re
import importlib.util as _ilu
import json
import requests
from datetime import datetime, timezone, timedelta
from crewai.tools import BaseTool
from agentmail import AgentMail

_bt = _ilu.spec_from_file_location("buffer_tool", os.path.join(os.path.dirname(__file__), "..", "shared", "tools", "buffer_tool.py"))
_bm = _ilu.module_from_spec(_bt); _bt.loader.exec_module(_bm)
PushTweetToBufferTool = _bm.PushTweetToBufferTool


def get_client() -> AgentMail:
    return AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])


def strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace to get readable plain text."""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def clean_json(raw: str) -> str:
    """
    Remove control characters that break json.loads().
    GPT-4 sometimes embeds newlines/tabs inside JSON string values.
    """
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)
    return cleaned


class FetchEmailsTool(BaseTool):
    name: str = "fetch_emails"
    description: str = (
        "Fetches emails from the AgentMail newsletter inbox received in the "
        "last 24 hours. Returns a list of thread dicts with thread_id, subject, "
        "from, and body_text fields."
    )

    def _run(self) -> str:
        client = get_client()
        inbox_id = os.environ["SENDER_INBOX_ID"]
        # Use last run timestamp if available, otherwise fall back to 24h
        last_run_path = os.path.join(os.path.dirname(__file__), "last_run.json")
        try:
            with open(last_run_path, "r") as f:
                last_run = json.load(f)
            since = datetime.fromisoformat(last_run["last_run_at"])
        except Exception:
            since = datetime.now(timezone.utc) - timedelta(hours=24)

        threads = client.inboxes.threads.list(
            inbox_id=inbox_id,
            after=since,
            limit=50,
            include_spam=True,
        )

        results = []
        for thread in threads.threads:
            try:
                detail = client.inboxes.threads.get(
                    inbox_id=inbox_id,
                    thread_id=thread.thread_id,
                )
                first_message = detail.messages[0] if detail.messages else None
                if not first_message:
                    continue

                # Prefer plain text; fall back to stripping HTML
                body = first_message.text or ""
                if not body and first_message.html:
                    body = strip_html(first_message.html)

                # Clean control characters from body before storing
                body = clean_json(body)

                results.append({
                    "thread_id": thread.thread_id,
                    "subject": first_message.subject or "(no subject)",
                    "from": first_message.from_ or "",
                    "body_text": body[:2000],  # capped to keep all emails in context
                })

            except Exception as e:
                results.append({
                    "thread_id": thread.thread_id,
                    "subject": "(error reading thread)",
                    "from": "",
                    "body_text": f"Error: {str(e)}",
                })

        return json.dumps(results, ensure_ascii=False)


class SendEmailTool(BaseTool):
    name: str = "send_email"
    description: str = (
        "Sends the daily briefing email via SMTP. "
        "Input must be a JSON string with keys: subject (str), body_html (str)."
    )

    def _run(self, payload: str) -> str:
        import smtplib
        import email.mime.multipart
        import email.mime.text
        try:
            data = json.loads(clean_json(payload))
            subject = data["subject"]
            body_html = data["body_html"]

            sender_email = os.environ["SENDER_EMAIL"]
            sender_password = os.environ["SENDER_APP_PASSWORD"]
            smtp_server = os.environ.get("SENDER_SMTP_SERVER", "smtp.zoho.com")
            smtp_port = int(os.environ.get("SENDER_SMTP_PORT", "465"))
            recipients = [r.strip() for r in os.environ["RECIPIENT_EMAIL"].split(",") if r.strip()]

            msg = email.mime.multipart.MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender_email
            msg["To"] = ", ".join(recipients)
            msg.attach(email.mime.text.MIMEText(body_html, "html"))

            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(sender_email, sender_password)
                server.sendmail(sender_email, recipients, msg.as_string())

            return "Email sent successfully."
        except Exception as e:
            return f"Email send error: {str(e)}"


class LoadStateTool(BaseTool):
    name: str = "load_state"
    description: str = (
        "Loads the existing daily_brief.json state file from disk. "
        "Returns a JSON string of existing stories for deduplication. "
        "Returns an empty list if the file does not exist yet."
    )

    def _run(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "daily_brief.json")
        if not os.path.exists(path):
            return json.dumps([])
        with open(path, "r") as f:
            data = json.load(f)
        # Drop stories older than 7 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        fresh = [
            s for s in data.get("stories", [])
            if datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")) > cutoff
        ]
        return json.dumps(fresh)


class SaveStateTool(BaseTool):
    name: str = "save_state"
    description: str = (
        "Saves the updated stories list to daily_brief.json on disk. "
        "Input must be a JSON array string of story objects."
    )

    def _run(self, stories_json: str) -> str:
        path = os.path.join(os.path.dirname(__file__), "daily_brief.json")
        try:
            stories = json.loads(clean_json(stories_json))
        except json.JSONDecodeError as e:
            return f"Error parsing stories JSON: {str(e)}. State not saved."

        with open(path, "w") as f:
            json.dump(
                {
                    "stories": stories,
                    "updated": datetime.now(timezone.utc).isoformat()
                },
                f,
                indent=2
            )
        return f"State saved. {len(stories)} stories on disk."


# PushTweetToBufferTool is imported from shared.tools.buffer_tool above.
