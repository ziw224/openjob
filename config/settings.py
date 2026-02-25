"""
config/settings.py – Central configuration for the job workflow.

Search behavior (keywords, experience levels, targets) is controlled by
config/search_config.json — edit that file directly, or run 'openjob setup'.
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

SEARCH_LOCATIONS  = SEARCH_CONFIG["locations"]
SORT_BY           = SEARCH_CONFIG.get("sort_by", "DD")
MAX_DAYS_OLD      = SEARCH_CONFIG.get("max_days_old", 0)
MAX_JOBS_PER_RUN  = SEARCH_CONFIG.get("max_candidates", 20)
JOB_WORKERS       = SEARCH_CONFIG.get("job_workers", 2)
FALLBACK_STAGES   = SEARCH_CONFIG.get("fallback", {}).get("stages", [])

# ── Flatten all categories into combined keyword / level / target lists ────────
_categories = SEARCH_CONFIG.get("categories", {})

SEARCH_KEYWORDS    = []
BOOST_KEYWORDS     = []
EXPERIENCE_LEVELS  = []
TARGET_JOBS        = 0
CATEGORY_CONFIGS   = {}   # name → raw dict, for per-category use

for _name, _cat in _categories.items():
    if _name.startswith("_"):
        continue
    CATEGORY_CONFIGS[_name] = _cat
    SEARCH_KEYWORDS   += _cat.get("keywords", [])
    BOOST_KEYWORDS    += _cat.get("boost_keywords", [])
    EXPERIENCE_LEVELS += _cat.get("experience_levels", [])
    TARGET_JOBS       += _cat.get("target_count", 0)

EXPERIENCE_LEVELS = list(dict.fromkeys(EXPERIENCE_LEVELS))  # deduplicate, preserve order

# ── Paths ──────────────────────────────────────────────────────────────────────
SEEN_JOBS_FILE = ROOT / "data" / "seen_jobs.json"
BASE_RESUME_HTML = ROOT / "resume" / "base_resume.html"
OUTPUT_DIR       = ROOT / "resume" / "output"

# ── Discord ────────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
