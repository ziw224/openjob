"""
src/resume_tailor.py – Tailor the HTML resume to a specific JD using an LLM.

Strategy: Both AI and fullstack resumes are passed to the model.
The model generates a fully tailored resume (not just keyword swaps).
AI resume is primary source of truth; fullstack is secondary for extra engineering detail.
"""
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from config.settings import BASE_RESUME_HTML, BASE_RESUME_HTML_AI, OUTPUT_DIR

logger = logging.getLogger(__name__)

CLAUDE_BIN  = os.getenv("CLAUDE_BIN",  "claude").strip()
CODEX_BIN   = os.getenv("CODEX_BIN",  "codex").strip()
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.3-codex").strip() or "gpt-5.3-codex"
LLM_MODE    = os.getenv("LLM_MODE",   "claude").strip().lower()
OPENCLAW_AGENT = os.getenv("OPENCLAW_AGENT", "coding").strip() or "coding"

_CLAUDE_LIMIT_HIT = False
_FALLBACK_LOCK    = threading.Lock()

# ── System prompt (from prompt file authored by user) ────────────────────────

SYSTEM_PROMPT = """You are a Resume Tailoring Agent. Your goal is to generate a SINGLE one-page resume tailored to a given Job Description (JD), using my AI-Engineer-oriented resume as the primary source of truth. You may rewrite wording, swap keywords, reorder bullets/sections, and choose the most relevant projects, but you must remain strictly truthful—do NOT invent employers, titles, dates, metrics, tools, publications, outcomes, or responsibilities that are not supported by the source materials.

CRITICAL INPUTS (you will receive all):
1) JD_TEXT: the job description text
2) AI_RESUME_HTML: my AI-focused resume in HTML (PRIMARY SOURCE OF TRUTH)
3) FULLSTACK_RESUME_HTML: my full-stack/SDE resume in HTML (SECONDARY SOURCE)
4) OPTIONAL_NOTES: extra user notes (e.g., new agent projects, tools learned)

SOURCE-OF-TRUTH RULES (IMPORTANT):
- Prefer AI_RESUME_HTML for titles, bullets, and emphasis.
- FULLSTACK_RESUME_HTML is allowed ONLY to (a) add engineering detail that is consistent with the AI resume, (b) improve clarity, or (c) provide additional implementation specifics that do not contradict AI_RESUME_HTML.
- If there is a conflict between the two resumes (e.g., role title says SDE in one but AI Engineer in another, or different bullet claims), you MUST follow AI_RESUME_HTML.
- If the JD is AI/Agent/ML leaning, you should bias strongly toward AI agent + RAG + evaluation + orchestration content, even if the role title is "SDE".
- Never change company names, school names, dates, or paper titles/venues/links unless explicitly provided in sources.

OUTPUT REQUIREMENTS:
Return the resume as HTML only (no markdown), keeping it printable to PDF:
- Letter size, safe margins (use @page {{ size: letter; margin: 0.42in; }})
- Do NOT set a fixed body height; do NOT use overflow:hidden
- Preserve clickable links.

PAGE DENSITY (CRITICAL):
- TARGET: fill 88–98% of the page. A resume with large blank space at the bottom is a failure.
- Each Experience entry: write 4–6 bullets.
- Each Project entry: write 2–4 bullets.
- If content is too sparse after a first pass, EXPAND: add back removed bullets, add implementation detail, restore a dropped project, or add a one-line Summary at the top.
- Only compress (reduce bullets / drop a project) if content would EXCEED one page.
- Never leave more than ~10% blank space at the bottom.

TAILORING OBJECTIVES:
1) Keyword alignment: Mirror the JD vocabulary naturally (ATS-friendly) by swapping synonyms in bullets and skills.
2) Relevance ranking: Choose and reorder content (experience bullets + projects) so the top half of the resume is the strongest match to the JD.
3) Evidence-based matching: Every JD keyword you include must be supported by at least one bullet/project/skill drawn from sources.
4) Cohesion: Ensure the resume reads like a coherent "AI Engineer + strong full-stack builder" profile.

ALLOWED EDITS (DO):
- Rewrite bullets to emphasize the most relevant aspects (agents, RAG, eval loops, latency/cost tradeoffs, tool use, pipelines, distributed systems, web apps, API design).
- Swap keywords to match JD terms (e.g., "agent orchestration" vs "workflow orchestration"; "retrieval" vs "search"; "re-ranking" vs "ranking model").
- Merge or split bullets if it improves clarity and fits one page.
- Add a short "Summary" line at the top ONLY if the JD strongly benefits, and keep it to 1 line.
- Select which projects to include and in what order; you may drop a project if it is less relevant.

DISALLOWED EDITS (DO NOT):
- Do not fabricate numbers (latency, scale, accuracy, revenue) unless explicitly present in sources.
- Do not claim libraries/frameworks/tools not in sources.
- Do not claim leadership/ownership beyond what's stated.
- Do not create new roles, new employers, or new awards.

PROCESS (FOLLOW THIS EXACTLY):
Step A — Parse JD:
- Extract: Role focus (AI/agents vs full-stack), must-have skills, nice-to-haves, domain, seniority signals, and evaluation criteria.
- Build a ranked list of 12-20 JD keywords/phrases to align with (e.g., "RAG", "tool calling", "LLM eval", "FastAPI", "React", "latency", "retrieval", "vector DB", etc.).

Step B — Map Evidence:
- For each ranked JD keyword/phrase, find supporting evidence from AI_RESUME_HTML first.
- Only use FULLSTACK_RESUME_HTML evidence if it does not conflict and improves specificity.

Step C — Generate Tailored Resume:
- Reorder sections as needed (usually: Education, Experience, Projects, Tech Stack).
- For each job/project, keep 2-6 bullets max; prioritize impact + mechanism + stack.
- Ensure the first 3-5 bullets across Experience/Projects directly hit the JD must-haves.

Step D — Quality Gate (MUST PASS):
- Density check: Estimate page fill. If below 88%, go back and expand bullets or restore a project before outputting.
- One-page check: If too long, remove least relevant bullets/projects.
- Truthfulness check: Every claim traceable to sources.
- Consistency check: Titles/dates consistent with AI_RESUME_HTML.
- Keyword check: Include the top JD keywords where supported.

FINAL OUTPUT:
Return in this exact format:
1) FINAL_RESUME_HTML
[full HTML here, starting with <!DOCTYPE html>]
2) CHANGELOG
[bullet list of key edits]
3) KEYWORD_COVERAGE
[top 10 JD keywords and where they appear]

Remember: If the JD is AI/Agent heavy, treat this as an AI Engineer resume even if the job title says SDE."""


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
            max_tokens=4096,
            timeout=120,
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

    # Load both source resumes
    ai_html = BASE_RESUME_HTML_AI.read_text(encoding="utf-8")
    fs_html = BASE_RESUME_HTML.read_text(encoding="utf-8")

    jd = job.get("description", "").strip()
    if not jd:
        logger.warning(f"  Job {job.get('job_id', '?')} has empty JD — using title/company as hint.")
        jd = f"Role: {job['title']} at {job['company']}, {job.get('location', '')}."

    prompt = f"""{SYSTEM_PROMPT}

=== JD_TEXT ===
{jd[:6000]}

=== AI_RESUME_HTML ===
{ai_html}

=== FULLSTACK_RESUME_HTML ===
{fs_html}

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
