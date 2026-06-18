# Alabama — AI-Powered Daily Newsletter Briefing Agent

Alabama reads your newsletter subscriptions and web sources every morning, extracts the most relevant stories, grades them, and publishes:
- A **daily briefing email** summarising all stories
- Up to **2 tweets** for the top stories (via Buffer) — optional
- **1 LinkedIn post** synthesising the best story/stories into a point of view (via Buffer) — optional

## How it works

1. **Fetch** — reads emails from an [AgentMail](https://agentmail.to) inbox (optional) and any URLs listed in `sources.md` (optional). At least one source must be configured.
2. **Extract** — GPT-4o parses all inputs, grades stories 1–10 across configurable categories (default: AI, Blockchain/Crypto, Fintech, Consulting)
3. **Filter** — only grade ≥ 5 stories make it into the briefing email
4. **Tweets** — GPT-4o writes 2 tweets for the top 2 stories, pushed to Buffer (skipped if `BUFFER_CHANNEL_ID` is not set)
5. **LinkedIn** — Claude Sonnet writes one post from the best grade-9/10 stories, in your voice, pushed to Buffer (skipped if `LINKEDIN_CHANNEL_ID` is not set)
6. **Email** — a CrewAI agent composes and sends the HTML briefing email via SMTP

## Setup

### 1. Clone and install

```bash
git clone https://github.com/pbucquet/Alabama.git
cd Alabama
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env — only the AI keys and SMTP settings are required
```

**Required:**
- `OPENAI_API_KEY` — GPT-4o for story extraction and tweet writing
- `ANTHROPIC_API_KEY` — Claude Sonnet for LinkedIn posts
- `SENDER_EMAIL`, `SENDER_APP_PASSWORD`, `SENDER_SMTP_SERVER`, `SENDER_SMTP_PORT` — SMTP account for the briefing email
- `RECIPIENT_EMAIL` — who receives the daily briefing

**Optional (step is skipped if absent):**
- `AGENTMAIL_API_KEY` + `SENDER_INBOX_ID` — newsletter inbox; leave blank if using web sources only
- `BUFFER_ACCESS_TOKEN` + `BUFFER_CHANNEL_ID` — tweet publishing; leave blank to skip
- `LINKEDIN_CHANNEL_ID` — LinkedIn publishing; leave blank to skip
- `TWITTER_CHANNEL_ID` — optional second Buffer channel for the LinkedIn companion tweet

See `.env.example` for the full reference.

### 3. Configure your voice and context

Fill in the three template files in `context/`:

| File | Purpose |
|---|---|
| `context/profile.md` | Who you are, your companies, your professional focus |
| `context/voice_and_tone.md` | How you write — style, tone, forbidden phrases |
| `context/topics_and_positions.md` | Your actual views on AI, crypto, fintech, consulting |

Alabama uses these files to write LinkedIn posts in your voice. The more specific you are, the better the output. All files are gitignored by default — keep them private.

You can also add files in `context/companies/` for detailed company descriptions.

### 4. Add web sources (optional)

Copy the template and add URLs to fetch daily alongside your newsletters:

```bash
cp sources.md.example sources.md
# Edit sources.md and add your URLs — one per line
```

Both RSS/Atom feeds and plain web pages are supported. **RSS feeds are strongly recommended** — they are faster, return structured per-item data, and support date filtering so Alabama only picks up items published since the last run. Plain web pages are scraped in full on every run.

`sources.md` is gitignored so your list stays private. Alabama runs fine with an empty or missing `sources.md`.

### 5. Set up your AgentMail inbox (optional)

Create an inbox at [agentmail.to](https://agentmail.to) and subscribe your newsletters to it. Set `SENDER_INBOX_ID` in your `.env` to the inbox address. Skip this step if you are using web sources only.

### 6. Run manually

```bash
source venv/bin/activate
python run_crew.py
```

### 7. Schedule daily runs (cron)

```bash
# Runs every day at 10:30 AM UTC — adjust to your timezone
30 10 * * * cd /path/to/alabama && source venv/bin/activate && python run_crew.py >> logs/crew.log 2>&1
```

A separate cleanup script removes processed emails older than 7 days:

```bash
# Runs daily after the main crew
0 11 * * * cd /path/to/alabama && source venv/bin/activate && python cleanup_inbox.py >> logs/cleanup.log 2>&1
```

## Category system

Stories are classified into a grid of parent categories × sub-categories. Both axes are fully configurable via `.env` — the defaults are:

|  | A — New Products | B — Funding/M&A | C — Use Cases | D — Impact/Trends |
|---|---|---|---|---|
| **1 — AI** | 1A | 1B | 1C | 1D |
| **2 — Blockchain/Crypto** | 2A | 2B | 2C | 2D |
| **3 — Fintech/Insurtech** | 3A | 3B | 3C | 3D |
| **4 — Consulting** | 4A | 4B | 4C | 4D |

To customise, set `STORY_CATEGORIES` and `STORY_SUBCATEGORIES` in your `.env` (see `.env.example` for the format). You can add, remove, or rename any category without touching the code.

LinkedIn post selection prioritises rarer categories. By default: **4 > 3 > 2 > 1** — the last category in `STORY_CATEGORIES` always wins ties.

## Requirements

- Python 3.10+
- OpenAI API key (GPT-4o) — required
- Anthropic API key (Claude Sonnet) — required
- An SMTP-enabled email account for sending the briefing — required
- AgentMail account — optional (needed for newsletter email ingestion)
- Buffer account with LinkedIn and/or Twitter channels connected — optional (needed for social publishing)
