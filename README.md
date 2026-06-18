# Alabama — AI-Powered Daily Newsletter Briefing Agent

Alabama reads your newsletter subscriptions every morning, extracts the most relevant stories, grades them, and publishes:
- A **daily briefing email** summarising all stories
- Up to **2 tweets** for the top stories (via Buffer)
- **1 LinkedIn post** synthesising the best story/stories into a point of view (via Buffer)

## How it works

1. **Fetch** — reads emails from an [AgentMail](https://agentmail.to) inbox since the last run
2. **Extract** — GPT-4o parses all emails, grades stories 1–10 across 4 categories (AI, Blockchain/Crypto, Fintech, Consulting)
3. **Filter** — only grade ≥ 5 stories make it into the briefing email
4. **Tweets** — GPT-4o writes 2 tweets for the top 2 stories, pushed to Buffer
5. **LinkedIn** — Claude Sonnet writes one post from the best grade-9/10 stories, in your voice, pushed to Buffer
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
# Edit .env and fill in all required values
```

See `.env.example` for the full list of required and optional variables.

### 3. Configure your voice and context

Fill in the three template files in `context/`:

| File | Purpose |
|---|---|
| `context/profile.md` | Who you are, your companies, your professional focus |
| `context/voice_and_tone.md` | How you write — style, tone, forbidden phrases |
| `context/topics_and_positions.md` | Your actual views on AI, crypto, fintech, consulting |

Alabama uses these files to write LinkedIn posts in your voice. The more specific you are, the better the output. All files are gitignored by default — keep them private.

You can also add files in `context/companies/` for detailed company descriptions.

### 4. Set up your AgentMail inbox

Create an inbox at [agentmail.to](https://agentmail.to) and subscribe your newsletters to it. Set `SENDER_INBOX_ID` in your `.env` to the inbox address.

### 5. Run manually

```bash
source venv/bin/activate
python run_crew.py
```

### 6. Schedule daily runs (cron)

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

Stories are classified into 16 sub-categories:

|  | A — New Products | B — Funding/M&A | C — Use Cases | D — Impact/Trends |
|---|---|---|---|---|
| **1 — AI** | 1A | 1B | 1C | 1D |
| **2 — Blockchain/Crypto** | 2A | 2B | 2C | 2D |
| **3 — Fintech/Insurtech** | 3A | 3B | 3C | 3D |
| **4 — Consulting** | 4A | 4B | 4C | 4D |

LinkedIn post selection prioritises rarer categories: **4 > 3 > 2 > 1** — so a consulting story will always beat an AI story when counts are equal.

## Requirements

- Python 3.10+
- OpenAI API key (GPT-4o)
- Anthropic API key (Claude Sonnet)
- AgentMail account
- Buffer account with LinkedIn and/or Twitter channels connected
- An SMTP-enabled email account for sending the briefing
