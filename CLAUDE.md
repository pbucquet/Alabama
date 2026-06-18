# Alabama — Claude Code Instructions

## What Alabama does
Alabama is a public, generalised AI-powered daily newsletter briefing agent.
It reads newsletter emails (via AgentMail) and/or web sources (RSS feeds, web pages listed in `sources.md`),
extracts and grades stories with GPT-4o, pushes tweets and a LinkedIn post via Buffer,
and sends a daily HTML briefing email via SMTP.

## Repo layout
- `run_crew.py` — main entry point; orchestrates all steps end-to-end
- `crew.py` — CrewAI crew for composing and sending the briefing email
- `tools.py` — AgentMail read/send wrappers + Buffer tweet tool
- `fetch_web_sources.py` — fetches RSS feeds and web pages from `sources.md`
- `linkedin_post.py` — selects top stories and writes/pushes the LinkedIn post
- `config.py` — parses `STORY_CATEGORIES` and `STORY_SUBCATEGORIES` from env
- `cleanup_inbox.py` — removes processed AgentMail threads older than 7 days
- `context/` — author voice/profile/topics markdown files (gitignored, private)
- `sources.md` — list of web URLs to fetch daily (gitignored, private)
- `sources.md.example` — committed template for `sources.md`
- `.env.example` — committed reference for all env vars

## Session rules
1. At the START of every session: `git pull` in this repo
2. At the END of every session: `git add -A && git commit -m "..." && git push`
3. Never leave uncommitted changes
4. Work on a feature branch for anything non-trivial

## Git / GitHub
- Remote: https://github.com/pbucquet/Alabama
- Default branch: `main`
- Commit message co-author line: `Co-Authored-By: Patrick and Claude Code`
- Never use `Co-Authored-By: Claude Sonnet` or any Anthropic email

## Environment variables
Required (Alabama exits if missing):
- `OPENAI_API_KEY` — GPT-4o for extraction, tweets, LinkedIn fallback
- `SENDER_EMAIL`, `SENDER_APP_PASSWORD`, `SENDER_SMTP_SERVER`, `SENDER_SMTP_PORT`
- `RECIPIENT_EMAIL`

Optional (relevant step is skipped if absent):
- `ANTHROPIC_API_KEY` — Claude Sonnet for LinkedIn posts; falls back to GPT-4o
- `AGENTMAIL_API_KEY` + `SENDER_INBOX_ID` — newsletter email ingestion
- `BUFFER_ACCESS_TOKEN` + `BUFFER_CHANNEL_ID` — tweet publishing
- `LINKEDIN_CHANNEL_ID` — LinkedIn publishing
- `TWITTER_CHANNEL_ID` — companion tweet from LinkedIn post
- `CONTEXT_DIR` — override for the context/ directory path
- `STORY_CATEGORIES` — pipe-separated `key:label` pairs (default: 4 categories)
- `STORY_SUBCATEGORIES` — pipe-separated `key:label` pairs (default: A–D)

## Category system
Stories get a two-character code: parent category (number) + sub-category (letter).
Both axes are configured via env vars in `STORY_CATEGORIES` / `STORY_SUBCATEGORIES`.
Defaults: `1=AI, 2=Blockchain, 3=Fintech, 4=Consulting` × `A=New Products, B=Funding, C=Use Cases, D=Impact`.
The last category in `STORY_CATEGORIES` wins LinkedIn tie-breaks (treated as rarest).
Config parsing lives in `config.py` — import from there, never hardcode category strings.

## Key behaviours to preserve
- GPT-4o extraction prompt is built dynamically from `config.py` — never hardcode categories in prompts
- Grade filter (≥5) happens in Python after GPT-4o returns all stories, not inside the prompt
- `last_run.json` stores the previous run timestamp; the fetch window uses a 12h overlap to avoid gaps
- Buffer pushes fall back to a local draft file (`tweets_draft.txt`, `linkedin_drafts.txt`) on failure — never raise exceptions that kill the run
- `sources.md` and `context/` are gitignored — never commit them

## EC2 deployment
- EC2 host: `ubuntu@34.237.132.163` (key: `~/.ssh/crewAI.pem`)
- Repo path on EC2: `/home/ubuntu/alabama`
- To deploy: push to GitHub, then `ssh -i ~/.ssh/crewAI.pem ubuntu@34.237.132.163 "cd /home/ubuntu/alabama && git pull origin main"`
- Do not edit files directly on EC2
- Cron: `30 10 * * *` (10:30 UTC) for `run_crew.py`, `0 11 * * *` for `cleanup_inbox.py`
