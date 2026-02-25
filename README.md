# openjob 🤖

Automated LinkedIn job-search agent with LLM-powered resume tailoring.

Every day, it scrapes LinkedIn for new roles, generates a tailored one-page resume and cover letter for each job, and saves everything locally. No manual work.

```
resume/output/
  └── 2026-02-25/
          ├── OpenAI/
          │     ├── YourName-Resume-OpenAI.pdf
          │     ├── YourName-CoverLetter-OpenAI.txt
          │     └── YourName-WhyOpenAI.txt
          └── Anthropic/
                └── ...
```

---

## Quick Start

```bash
git clone https://github.com/yourusername/openjob
cd openjob
bash install.sh
```

Then:

```bash
# 1. Set up your info
cp .env.example .env
# Edit .env — add your name, email, OpenAI key

# 2. Add your resumes
# Edit resume/base_resume_ai.html  (AI/ML roles)
# Edit resume/base_resume.html     (Full-stack/SDE roles)

# 3. Customize search keywords
# Edit config/search_config.json

# 4. Run
openjob run
```

---

## Commands

| Command | Description |
|---|---|
| `openjob run` | Full daily pipeline (scrape → tailor → PDF) |
| `openjob retry <url>` | Run pipeline for a single LinkedIn URL |
| `openjob retry-day [date]` | Re-run failed jobs for a date |
| `openjob status` | Show today's output summary |
| `openjob model <backend>` | Switch LLM backend |

---

## Configuration

### `.env`

```env
CANDIDATE_NAME="Your Name"
EMAIL="you@email.com"
LINKEDIN="linkedin.com/in/yourhandle"
PORTFOLIO="yoursite.dev"

LLM_MODE=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# Optional Discord notifications
# DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
```

### LLM Backends

| Mode | Requirement |
|---|---|
| `openai` | OpenAI API key (recommended) |
| `claude` | [Claude Code CLI](https://docs.anthropic.com/claude-code) |
| `codex` | Codex CLI |

### Search Config (`config/search_config.json`)

```json
{
  "keywords": ["AI Engineer", "ML Engineer", "Applied AI"],
  "location": "San Francisco Bay Area",
  "experience_levels": [2, 3, 4],
  "target_ai_jobs": 5,
  "target_sde_jobs": 5
}
```

---

## Setup Guide

### Resumes

Edit the two HTML files in `resume/`:
- `base_resume_ai.html` — used for AI/ML/agent roles
- `base_resume.html` — used for SDE/full-stack roles

The LLM reads both files and generates a tailored version for each job.

### LinkedIn Login

On first run, Playwright will open a browser window. Log in to LinkedIn manually. The session is saved and reused for future runs.

### Cron (automatic daily runs)

```bash
# Run every day at 9 AM
0 9 * * * cd /path/to/openjob && openjob run >> logs/cron.log 2>&1
```

---

## Output

All files are saved to `resume/output/YYYY-MM-DD/{Company}/`:

- `YourName-Resume-{Company}.pdf` — tailored one-page resume
- `YourName-CoverLetter-{Company}.txt` — cover letter
- `YourName-Why{Company}.txt` — "why this company" note

---

## Requirements

- Python 3.10+
- An OpenAI API key (or Claude/Codex access)
- A LinkedIn account

---

## License

MIT
