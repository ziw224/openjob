"""
src/resume_tailor.py – Tailor the HTML resume to a specific JD using an LLM.

Strategy: base_resume.html is passed to the LLM with the full JD.
The model generates a fully tailored, keyword-optimized one-page resume.
"""
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from config.settings import BASE_RESUME_HTML, OUTPUT_DIR

logger = logging.getLogger(__name__)

CLAUDE_BIN  = os.getenv("CLAUDE_BIN",  "claude").strip()
CODEX_BIN   = os.getenv("CODEX_BIN",  "codex").strip()
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.3-codex").strip() or "gpt-5.3-codex"
LLM_MODE    = os.getenv("LLM_MODE",   "claude").strip().lower()
OPENCLAW_AGENT = os.getenv("OPENCLAW_AGENT", "coding").strip() or "coding"

_CLAUDE_LIMIT_HIT = False
_FALLBACK_LOCK    = threading.Lock()

# ── System prompt (from prompt file authored by user) ────────────────────────

SYSTEM_PROMPT = """You are a Resume Tailoring Agent. Generate a SINGLE one-page resume tailored to the given Job Description (JD), using RESUME_HTML as the sole source of truth.

You may rewrite wording, swap keywords, reorder bullets/sections, and choose the most relevant projects, but you must remain strictly truthful — do NOT invent employers, titles, dates, metrics, tools, or responsibilities not present in the source.

INPUTS:
1) JD_TEXT: the job description
2) RESUME_HTML: the candidate's resume in HTML (source of truth)
3) OPTIONAL_NOTES: extra context (may be empty)

OUTPUT REQUIREMENTS:
- Return valid HTML only (no markdown). Must print to one page.
- Letter size, @page { size: letter; margin: 0.42in; }
- Do NOT set a fixed body height or overflow:hidden.
- Preserve all links.

PAGE DENSITY (CRITICAL):
- Target: fill 88–98% of the page. Large blank space at the bottom = failure.
- Experience entries: 4–6 bullets each.
- Project entries: 2–4 bullets each.
- If too sparse: expand bullets, add detail, restore a dropped project.
- If too long: trim least-relevant bullets or drop a project.

TAILORING OBJECTIVES:
1) Mirror JD vocabulary naturally (ATS-friendly keyword alignment).
2) Reorder content so the top half is the strongest match to the JD.
3) Every JD keyword included must be supported by source evidence.

PROCESS:
A) Parse JD → extract must-haves, nice-to-haves, seniority signals, top 10-15 keywords.
B) Map each keyword to evidence in RESUME_HTML.
C) Generate tailored resume — reorder/rewrite bullets to front-load JD match.
D) Quality gate: density ≥88%, one page, all claims traceable to source.

FINAL OUTPUT (exact format):
1) FINAL_RESUME_HTML
[full HTML starting with <!DOCTYPE html>]
2) CHANGELOG
[bullet list of key edits]
3) KEYWORD_COVERAGE
[top 10 JD keywords and where they appear]"""


def _sanitize(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)[:40].strip("_")


def _call_llm(prompt: str) -> str | None:
    """Call the configured LLM (claude / codex / openclaw). Returns raw output or None."""
    global _CLAUDE_LIMIT_HIT
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    if LLM_MODE == "openai":
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096, timeout=120,
        )
        return resp.choices[0].message.content.strip()

    elif LLM_MODE == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            max_tokens=4096,
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
            max_tokens=4096, timeout=120,
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
            max_tokens=4096, timeout=120,
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
            max_tokens=4096, timeout=180,
        )
        return resp.choices[0].message.content.strip()

    elif LLM_MODE == "openclaw":
        with _FALLBACK_LOCK:
            result = subprocess.run(
                ["openclaw", "agent", "--local", "--agent", OPENCLAW_AGENT, "--message", prompt],
                capture_output=True, text=True, timeout=360,
            )
        if result.returncode != 0:
            logger.error(f"  OpenClaw call failed ({result.returncode}): {(result.stderr or '')[:300]}")
            return None
        return result.stdout.strip()

    elif LLM_MODE == "codex":
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tf:
            out_file = tf.name
        try:
            _codex_env = os.environ.copy()
            _nvm_node_dir = str(Path(CODEX_BIN).parent)
            _codex_env["PATH"] = _nvm_node_dir + os.pathsep + _codex_env.get("PATH", "")
            result = subprocess.run(
                [CODEX_BIN, "exec",
                 "--model", CODEX_MODEL,
                 "--sandbox", "read-only",
                 "--skip-git-repo-check",
                 "--output-last-message", out_file, "-"],
                input=prompt, capture_output=True, text=True, timeout=480, env=_codex_env,
            )
            if result.returncode != 0:
                logger.error(f"  Codex call failed ({result.returncode}): {(result.stderr or result.stdout or '')[:300]}")
                return None
            return Path(out_file).read_text(encoding="utf-8").strip()
        finally:
            Path(out_file).unlink(missing_ok=True)

    else:  # claude
        if _CLAUDE_LIMIT_HIT:
            logger.error("  Claude quota already hit. Set LLM_MODE=codex to continue.")
            return None
        result = subprocess.run(
            [CLAUDE_BIN, "--dangerously-skip-permissions", "--print"],
            input=prompt, capture_output=True, text=True, timeout=360, env=env,
        )
        if result.returncode != 0:
            err = result.stderr or result.stdout or ""
            if "hit your limit" in err.lower() or "resets" in err.lower():
                _CLAUDE_LIMIT_HIT = True
            logger.error(f"  Claude CLI failed ({result.returncode}): {err[:400]}")
            return None
        return result.stdout.strip()


def _extract_html(raw: str) -> str | None:
    """Extract the FINAL_RESUME_HTML block from the LLM output."""
    # Strategy 1: find <!DOCTYPE html> ... </html>
    m = re.search(r"(<!DOCTYPE\s+html[\s\S]*?</html>)", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Strategy 2: find <html ...> ... </html>
    m = re.search(r"(<html[\s\S]*?</html>)", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Strategy 3: look after "FINAL_RESUME_HTML" marker, take everything up to CHANGELOG
    m = re.search(r"FINAL_RESUME_HTML\s*([\s\S]*?)(?:2\)|CHANGELOG)", raw, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        # Strip markdown fences if present
        fence = re.search(r"```(?:html)?\n?([\s\S]+?)```", candidate)
        return fence.group(1).strip() if fence else candidate

    return None


def _extract_section(raw: str, section: str) -> str:
    """Extract CHANGELOG or KEYWORD_COVERAGE from LLM output."""
    pattern = rf"{section}\s*([\s\S]*?)(?=\n(?:1\)|2\)|3\)|FINAL_RESUME|CHANGELOG|KEYWORD)|$)"
    m = re.search(pattern, raw, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def tailor_resume(job: dict, output_dir: Path | None = None) -> Path | None:
    """
    Generate a fully tailored HTML resume for the given job using both AI + FS resumes.
    Returns path to saved HTML file, or None on failure.
    """
    out_dir = output_dir if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load source resume
    resume_html = BASE_RESUME_HTML.read_text(encoding="utf-8")

    jd = job.get("description", "").strip()
    if not jd:
        logger.warning(f"  Job {job.get('job_id', '?')} has empty JD — using title/company as hint.")
        jd = f"Role: {job['title']} at {job['company']}, {job.get('location', '')}."

    prompt = f"""{SYSTEM_PROMPT}

=== JD_TEXT ===
{jd[:6000]}

=== RESUME_HTML ===
{resume_html}

=== OPTIONAL_NOTES ===
(none)
"""

    logger.info(f"  Tailoring ({LLM_MODE}): {job['title']} @ {job['company']} …")

    try:
        raw = _call_llm(prompt)
    except subprocess.TimeoutExpired:
        logger.error("  LLM call timed out.")
        return None
    except Exception as e:
        logger.error(f"  LLM call exception: {e}")
        return None

    if not raw:
        logger.error("  LLM returned empty output.")
        return None

    # Extract HTML
    tailored_html = _extract_html(raw)
    if not tailored_html:
        logger.error(f"  Could not extract HTML from LLM output (got: {raw[:200]!r})")
        return None

    # Log changelog and keyword coverage
    changelog = _extract_section(raw, "CHANGELOG")
    keywords  = _extract_section(raw, "KEYWORD_COVERAGE")
    if changelog:
        logger.info(f"  📝 Changelog:\n{changelog[:600]}")
    if keywords:
        logger.info(f"  🔑 Keywords:\n{keywords[:400]}")

    # Save HTML
    company_slug = _sanitize(job["company"])
    job_id = job.get("job_id") or (job.get("url", "").rstrip("/").split("/")[-1]) or "0"
    html_path = out_dir / f"{job_id}_{company_slug}.html"
    html_path.write_text(tailored_html, encoding="utf-8")
    logger.info(f"  ✅ Saved → {html_path.name}")

    return html_path
