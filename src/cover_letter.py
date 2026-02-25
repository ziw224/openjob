"""
src/cover_letter.py – Generate cover letter and "Why [Company]" answer via Claude CLI.

Outputs two plain-text files per job:
  - {CANDIDATE_NAME}-CoverLetter-{Company}.txt
  - {CANDIDATE_NAME}-Why{Company}.txt

Personal config: set in .env or config/candidate.txt (see config/candidate.txt.example)
"""
import logging
import os
import re
import subprocess
import threading
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# ── LLM config (from env) ──────────────────────────────────────────────────────
CLAUDE_BIN   = os.getenv("CLAUDE_BIN", "claude").strip()
LLM_MODE     = os.getenv("LLM_MODE", "claude").strip().lower()   # claude | codex | openclaw
OPENCLAW_AGENT = os.getenv("OPENCLAW_AGENT", "coding").strip() or "coding"
CODEX_BIN    = os.getenv("CODEX_BIN", "codex").strip()
CODEX_MODEL  = os.getenv("CODEX_MODEL", "gpt-5.3-codex").strip() or "gpt-5.3-codex"

# Once Claude reports quota limit in this process, skip further Claude calls.
_CLAUDE_LIMIT_HIT = False
_FALLBACK_LOCK = threading.Lock()

# ── Candidate config (from env or config/candidate.txt) ───────────────────────
CANDIDATE_NAME      = os.getenv("CANDIDATE_NAME", "Your Name")
CANDIDATE_EMAIL     = os.getenv("CANDIDATE_EMAIL", "your@email.com")
CANDIDATE_PORTFOLIO = os.getenv("CANDIDATE_PORTFOLIO", "")
CANDIDATE_LINKEDIN  = os.getenv("CANDIDATE_LINKEDIN", "")

def _load_candidate_bio() -> str:
    """Load bio from config/candidate.txt (gitignored), fallback to CANDIDATE_BIO env var."""
    bio_file = Path(__file__).parent.parent / "config" / "candidate.txt"
    if bio_file.exists():
        return bio_file.read_text(encoding="utf-8").strip()
    env_bio = os.getenv("CANDIDATE_BIO", "")
    if env_bio:
        return env_bio.strip()
    return f"Candidate: {CANDIDATE_NAME}\n(Add your background to config/candidate.txt)"

CANDIDATE_BIO = _load_candidate_bio()


# ── Prompts ────────────────────────────────────────────────────────────────────

COVER_LETTER_PROMPT = """You are an expert career coach writing a cover letter for {candidate_name}.

CANDIDATE BACKGROUND:
{bio}

ROLE: {title} at {company} ({location})

JOB DESCRIPTION:
{jd}

Write a polished, specific cover letter body (3–4 paragraphs, under 320 words total):
- Paragraph 1: Open with genuine enthusiasm for THIS specific company and role. Reference something real about the company's product/mission from the JD.
- Paragraph 2: Highlight 2 of the most relevant experiences/projects from her background that directly map to the JD requirements.
- Paragraph 3: Connect her research or a unique skill to what this company needs. Make it feel non-generic.
- Paragraph 4 (short): Express excitement to contribute, call to action.

Tone: Warm, confident, and specific — not stiff corporate language. Sound like a real person.

IMPORTANT:
- Do NOT include "Dear Hiring Manager" or any header — only the 3–4 paragraph body.
- Do NOT include sign-off / signature lines.
- Plain text only, no markdown, no bullet points.
- Keep it under 320 words.
"""

WHY_COMPANY_PROMPT = """Write a "Why do you want to work at {company}?" answer for {candidate_name} applying for {title}.

CANDIDATE BACKGROUND:
{bio}

JOB DESCRIPTION (read carefully — the answer must stay grounded in THIS specific role):
{jd_excerpt}

Follow this formula STRICTLY — 3–5 sentences total, no more:

SENTENCE 1–2 (Company highlight): Pick ONE specific and concrete thing about {company} that directly relates to THE ACTUAL WORK described in the JD for this {title} role — the specific tech stack, engineering problems, team structure, or product decisions mentioned in the responsibilities/requirements section. Must come from what the candidate will actually DO day-to-day, not the company's overall product or mission. Include a specific detail that shows you actually read the JD. Keep it brief.

SENTENCE 2–3 (Why it matters): Explain WHY that specific thing is meaningful — what real problem does it solve, who does it affect, what's hard about it from an engineering perspective. Go one level deeper than the obvious. Show analytical thinking, not just "it's impressive."

SENTENCE 3–5 (Link to You — MOST IMPORTANT): Connect that company/role strength DIRECTLY to the candidate's growth as a {title}. Be specific: what skill will she develop in THIS role at THIS company that she can't develop elsewhere? What from her background (specific projects, research, internship) makes her genuinely care about THIS engineering problem? Must feel personal and earned — NOT "I'll learn a lot" or "I'm passionate about software."

CRITICAL RULES:
- Stay grounded in what the candidate will ACTUALLY DO in this role — if it's fullstack SWE, talk about fullstack engineering challenges; if it's backend, talk about backend; do NOT bring up AI/ML/research unless the JD explicitly lists them as job responsibilities
- If the company happens to have AI features but the role itself is fullstack/SWE, focus on the SWE engineering work, not the AI product
- No superlatives or generic praise ("best", "leading", "innovative", "cutting-edge")
- No facts everyone knows ("used by millions", "top company", "fast-growing")
- The link-to-you section must name specific projects or experiences from the candidate's background
- Plain text, no markdown, no bullet points
- Output ONLY the answer — no intro phrases, no labels
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_claude(prompt: str, label: str) -> str | None:
    """Run selected model backend (LLM_MODE=claude|openclaw). No automatic fallback."""
    try:
        global _CLAUDE_LIMIT_HIT
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        if LLM_MODE == "openai":
            import openai
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500, timeout=60,
            )
            return resp.choices[0].message.content.strip()

        elif LLM_MODE == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            msg = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()

        elif LLM_MODE == "gemini":
            import openai
            client = openai.OpenAI(
                api_key=os.getenv("GEMINI_API_KEY"),
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            resp = client.chat.completions.create(
                model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500, timeout=60,
            )
            return resp.choices[0].message.content.strip()

        elif LLM_MODE == "groq":
            import openai
            client = openai.OpenAI(
                api_key=os.getenv("GROQ_API_KEY"),
                base_url="https://api.groq.com/openai/v1",
            )
            resp = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500, timeout=60,
            )
            return resp.choices[0].message.content.strip()

        elif LLM_MODE == "ollama":
            import openai
            client = openai.OpenAI(
                api_key="ollama",
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1",
            )
            resp = client.chat.completions.create(
                model=os.getenv("OLLAMA_MODEL", "llama3.1"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500, timeout=120,
            )
            return resp.choices[0].message.content.strip()

        elif LLM_MODE == "openclaw":
            with _FALLBACK_LOCK:
                fb = subprocess.run(
                    [
                        "openclaw", "agent", "--local",
                        "--agent", OPENCLAW_AGENT,
                        "--message", prompt,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
            if fb.returncode != 0:
                logger.error(f"  OpenClaw call failed for {label} (code {fb.returncode}): {(fb.stderr or '')[:300]}")
                return None
            output = fb.stdout.strip()
        elif LLM_MODE == "codex":
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tf:
                out_file = tf.name
            try:
                # Inject nvm node path so codex can find node in cron environment
                _codex_env = os.environ.copy()
                _nvm_node_dir = str(Path(CODEX_BIN).parent)
                _codex_env["PATH"] = _nvm_node_dir + os.pathsep + _codex_env.get("PATH", "")
                _codex_cwd = tempfile.mkdtemp()   # isolated dir so codex can't pollute repo root
                cx = subprocess.run(
                    [
                        CODEX_BIN, "exec",
                        "--model", CODEX_MODEL,
                        "--sandbox", "read-only",
                        "--skip-git-repo-check",
                        "--output-last-message", out_file,
                        "-",
                    ],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=_codex_env,
                    cwd=_codex_cwd,
                )
                if cx.returncode != 0:
                    logger.error(f"  Codex call failed for {label} (code {cx.returncode}): {(cx.stderr or cx.stdout or '')[:300]}")
                    return None
                output = Path(out_file).read_text(encoding="utf-8").strip()
            finally:
                try:
                    Path(out_file).unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            if _CLAUDE_LIMIT_HIT:
                logger.error(f"  Claude quota already hit in this run for {label}. Set LLM_MODE=codex.")
                return None
            result = subprocess.run(
                [CLAUDE_BIN, "--dangerously-skip-permissions", "--print"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
            if result.returncode != 0:
                err_full = (result.stderr or result.stdout or "")
                if "hit your limit" in err_full.lower() or "resets" in err_full.lower():
                    _CLAUDE_LIMIT_HIT = True
                logger.error(f"  Claude CLI failed for {label} (code {result.returncode}): {err_full[:500]}")
                return None
            output = result.stdout.strip()
        if not output:
            logger.error(f"  Claude returned empty output for {label}")
            return None

        # Strip common preamble phrases Claude sometimes adds
        for preamble in [
            "Here's the answer:",
            "Here's the cover letter:",
            "Here's the cover letter body:",
            "Here is the answer:",
            "Here is the cover letter:",
        ]:
            if output.startswith(preamble):
                output = output[len(preamble):].strip()

        return output
    except subprocess.TimeoutExpired:
        logger.error(f"  Claude CLI timed out for {label}")
        return None
    except Exception as e:
        logger.error(f"  Claude CLI exception for {label}: {e}")
        return None


def _sanitize_company(name: str) -> str:
    """Remove special chars for filenames."""
    return re.sub(r"[^a-zA-Z0-9]", "", name)


def _format_cover_letter(body: str, job: dict) -> str:
    """Wrap the Claude-generated body with a proper header and sign-off."""
    today = date.today().strftime("%B %d, %Y")
    company = job.get("company", "")
    title   = job.get("title", "")
    header = f"""{today}

Hiring Team
{company}

Re: {title}

Dear Hiring Manager,

"""
    contact_parts = [p for p in [CANDIDATE_EMAIL, CANDIDATE_PORTFOLIO, CANDIDATE_LINKEDIN] if p]
    contact_line  = " | ".join(contact_parts)
    footer = f"\n\nSincerely,\n{CANDIDATE_NAME}\n{contact_line}\n"
    return header + body + footer


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_cover_letter(job: dict, output_dir: Path) -> dict[str, Path | None]:
    """
    Generate cover letter and "Why [Company]" answer for a job.

    Args:
        job: dict with keys title, company, location, description
        output_dir: directory to save files (same as resume output dir)

    Returns:
        {
            "cover_letter": Path | None,
            "why_company":  Path | None,
        }
    """
    company   = job.get("company", "Company")
    title     = job.get("title", "Software Engineer")
    location  = job.get("location", "")
    jd        = job.get("description", "").strip()

    if not jd:
        jd = f"Role: {title} at {company}, {location}."

    company_slug = _sanitize_company(company)
    results: dict[str, Path | None] = {"cover_letter": None, "why_company": None}

    # ── 1. Cover Letter ────────────────────────────────────────────────────────
    logger.info(f"  Generating cover letter for {title} @ {company} …")
    cl_prompt = COVER_LETTER_PROMPT.format(
        candidate_name=CANDIDATE_NAME,
        bio=CANDIDATE_BIO,
        title=title,
        company=company,
        location=location,
        jd=jd[:5000],
    )
    cl_body = _run_claude(cl_prompt, f"cover_letter:{company}")
    if cl_body:
        full_letter = _format_cover_letter(cl_body, job)
        cl_path = output_dir / f"{CANDIDATE_NAME}-CoverLetter-{company}.txt"
        cl_path.write_text(full_letter, encoding="utf-8")
        logger.info(f"  ✅ Cover letter saved → {cl_path.name}")
        results["cover_letter"] = cl_path
    else:
        logger.warning(f"  ⚠️ Cover letter generation failed for {company}")

    # ── 2. Why [Company] ──────────────────────────────────────────────────────
    logger.info(f"  Generating 'Why {company}' answer …")
    why_prompt = WHY_COMPANY_PROMPT.format(
        candidate_name=CANDIDATE_NAME,
        company=company,
        title=title,
        bio=CANDIDATE_BIO,
        jd_excerpt=jd[:2500],
    )
    why_text = _run_claude(why_prompt, f"why:{company}")
    if why_text:
        why_path = output_dir / f"{CANDIDATE_NAME}-Why{company_slug}.txt"
        why_path.write_text(why_text, encoding="utf-8")
        logger.info(f"  ✅ Why-{company} saved → {why_path.name}")
        results["why_company"] = why_path
    else:
        logger.warning(f"  ⚠️ Why-company generation failed for {company}")

    return results
