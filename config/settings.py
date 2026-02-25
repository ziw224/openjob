"""
config/settings.py – Central configuration for the job workflow.

Search behavior (keywords, experience levels, targets) is controlled by
config/search_config.json — edit that file directly, no code changes needed.
"""
import json
import os
from pathlib import Path

# ── Project Root ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent

# ── Load search_config.json ───────────────────────────────────────────────────
_cfg_path = ROOT / "config" / "search_config.json"
with open(_cfg_path) as _f:
    SEARCH_CONFIG = json.load(_f)

_sde = SEARCH_CONFIG["categories"]["sde"]
_ai  = SEARCH_CONFIG["categories"]["ai"]

SEARCH_LOCATIONS      = SEARCH_CONFIG["locations"]
SORT_BY               = SEARCH_CONFIG.get("sort_by", "DD")
MAX_DAYS_OLD          = SEARCH_CONFIG.get("max_days_old", 0)
MAX_JOBS_PER_RUN      = SEARCH_CONFIG.get("max_candidates", 30)
JOB_WORKERS           = SEARCH_CONFIG.get("job_workers", 2)

# Per-category settings
SDE_KEYWORDS          = _sde["keywords"]
SDE_BOOST_KEYWORDS    = _sde.get("boost_keywords", [])
SDE_EXPERIENCE_LEVELS = _sde["experience_levels"]
TARGET_SDE_JOBS       = _sde["target_count"]

AI_KEYWORDS           = _ai["keywords"]
AI_BOOST_KEYWORDS     = _ai.get("boost_keywords", [])
AI_EXPERIENCE_LEVELS  = _ai["experience_levels"]
TARGET_AI_JOBS        = _ai["target_count"]

# Fallback stages (list of dicts, run in order when targets not met)
FALLBACK_STAGES = SEARCH_CONFIG.get("fallback", {}).get("stages", [])

# Combined (for backwards compat)
SEARCH_KEYWORDS       = SDE_KEYWORDS + AI_KEYWORDS
EXPERIENCE_LEVELS     = list(set(SDE_EXPERIENCE_LEVELS + AI_EXPERIENCE_LEVELS))

# ── Paths ──────────────────────────────────────────────────────────────────────
SEEN_JOBS_FILE   = ROOT / "data" / "seen_jobs.json"
BASE_RESUME_HTML    = ROOT / "resume" / "base_resume.html"
BASE_RESUME_HTML_AI = ROOT / "resume" / "base_resume_ai.html"

# Keywords in job TITLE that indicate an AI/ML Engineer role (title match → AI resume)
AI_TITLE_KEYWORDS = [
    "ai engineer", "ml engineer", "machine learning engineer",
    "ai/ml", "artificial intelligence", "machine learning",
]

# Keywords that indicate a Full Stack / SWE role (title match → FS resume, overrides JD scan)
FS_TITLE_KEYWORDS = [
    "full stack", "fullstack", "full-stack",
    "frontend", "front-end", "backend", "back-end",
    "software engineer", "software developer", "swe",
    "web engineer", "web developer",
]

# Keywords that indicate an AI/ML Engineer role (used only when title is ambiguous)
AI_ROLE_KEYWORDS = [
    "ai engineer", "ml engineer", "machine learning engineer",
    "llm", "large language model", "langchain", "rag", "retrieval",
    "agentic", "generative ai", "gen ai", "gpt", "embedding",
    "fine-tun", "huggingface", "pytorch", "tensorflow",
    "computer vision", "nlp", "natural language",
]
OUTPUT_DIR       = ROOT / "resume" / "output"

# ── Discord ────────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
