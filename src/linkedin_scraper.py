"""
src/linkedin_scraper.py – Scrape LinkedIn job postings via Playwright (no login required).

Optimized two-phase approach:
  Phase 1 – collect job cards from public LinkedIn search (with pagination + early stopping)
  Phase 2 – fetch full JD text in parallel (multi-tab)
"""
import json
import logging
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config.settings import (
    SEARCH_LOCATIONS,
    SORT_BY,
    MAX_DAYS_OLD,
    MAX_JOBS_PER_RUN,
    SEEN_JOBS_FILE,
    SDE_KEYWORDS, SDE_BOOST_KEYWORDS, SDE_EXPERIENCE_LEVELS, TARGET_SDE_JOBS,
    AI_KEYWORDS,  AI_BOOST_KEYWORDS,  AI_EXPERIENCE_LEVELS,  TARGET_AI_JOBS,
    FALLBACK_STAGES,
)

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
MAX_PAGES_PER_SEARCH = 3        # max pages to paginate per search query
JD_FETCH_WORKERS     = 4        # parallel tabs for Phase 2 JD fetching
CARDS_PER_PAGE       = 25       # LinkedIn shows ~25 cards per page

# Type alias for progress callback
ProgressFn = Callable[[str], None] | None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_seen(path: Path) -> set[str]:
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        return set(data.get("seen_ids", []))
    return set()


def _save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.exists():
        with open(path) as f:
            existing = json.load(f)
    existing["seen_ids"] = list(seen)
    existing["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


def _safe_sleep(base: float = 1.5, jitter: float = 1.0) -> None:
    time.sleep(base + random.uniform(0, jitter))


def _days_ago(posted_date_str: str) -> int | None:
    """Parse a LinkedIn datetime string → how many days ago."""
    if not posted_date_str:
        return None
    try:
        if len(posted_date_str) == 10:
            posted = datetime.strptime(posted_date_str, "%Y-%m-%d")
            return max(0, (datetime.utcnow() - posted).days)
        posted = datetime.fromisoformat(posted_date_str.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - posted).days)
    except Exception:
        return None


def _build_search_url(keyword: str, location: str, exp_levels: list[int], start: int = 0) -> str:
    """Build a LinkedIn search URL with optional pagination offset."""
    query   = keyword.replace(" ", "%20")
    loc     = location.replace(" ", "%20").replace(",", "%2C")
    exp_str = "%2C".join(str(e) for e in exp_levels)
    url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={query}&location={loc}"
        f"&f_E={exp_str}&sortBy={SORT_BY}"
    )
    if start > 0:
        url += f"&start={start}"
    return url


def _location_match(job_location: str, query_location: str) -> bool:
    """Strict location filter to avoid unrelated cities from LinkedIn broad matching."""
    jl = (job_location or "").lower()
    ql = (query_location or "").lower()

    if not jl:
        return False

    # Remote query: only keep remote-labeled jobs
    if "remote" in ql:
        return "remote" in jl

    # City query: require city token match (e.g. "san francisco")
    city = ql.split(",")[0].strip()
    return city in jl


def _parse_cards(page, seen: set[str], existing_ids: set[str],
                 keyword: str, location: str, max_days: int) -> list[dict]:
    """Parse job cards from the current page. Returns new candidates only."""
    cards = page.query_selector_all(".job-search-card")
    results = []

    for card in cards:
        try:
            link_el  = card.query_selector("a")
            title_el = card.query_selector(".base-search-card__title")
            comp_el  = card.query_selector(".base-search-card__subtitle")
            loc_el   = card.query_selector(".job-search-card__location")
            time_el  = card.query_selector("time")

            href        = link_el.get_attribute("href") if link_el else ""
            title       = title_el.inner_text().strip() if title_el else ""
            company     = comp_el.inner_text().strip()  if comp_el  else ""
            loc_str     = loc_el.inner_text().strip()   if loc_el   else location
            posted_date = (time_el.get_attribute("datetime") or "") if time_el else ""

            m = re.search(r"[/-](\d{7,})", href or "")
            if not m:
                continue
            job_id = m.group(1)

            # Skip seen or already collected
            if job_id in seen or job_id in existing_ids:
                continue

            # Strict location filter (LinkedIn often returns broad nearby results)
            if not _location_match(loc_str, location):
                continue

            # Filter by recency
            days_old = _days_ago(posted_date)
            if max_days > 0 and days_old is not None and days_old > max_days:
                continue

            results.append({
                "job_id":      job_id,
                "title":       title,
                "company":     company,
                "location":    loc_str,
                "url":         href.split("?")[0],
                "keyword":     keyword,
                "posted_date": posted_date,
                "days_old":    days_old if days_old is not None else -1,
            })
        except Exception:
            continue

    return results


def _fetch_jd(page, candidate: dict) -> dict:
    """Fetch the full JD for a single candidate using an existing page."""
    description = ""
    try:
        page.goto(candidate["url"], timeout=25_000)
        page.wait_for_timeout(3000)
        for sel in [
            ".show-more-less-html__markup",
            "#job-details",
            ".description__text",
        ]:
            el = page.query_selector(sel)
            if el:
                description = el.inner_text().strip()
                break
    except Exception as e:
        logger.warning(f"  JD fetch failed for {candidate['job_id']}: {e}")

    _safe_sleep(1.0, 0.5)
    return {**candidate, "description": description}


# ── Main Scraper ───────────────────────────────────────────────────────────────

def _effective_keywords(base: list[str], boost: list[str], exp_levels: list[int]) -> list[str]:
    """For entry-level searches (exp level 2), include boost keywords like New Grad."""
    kws = list(base)
    if 2 in exp_levels and boost:
        kws.extend(boost)
    # de-dup while preserving order
    return list(dict.fromkeys(kws))


def scrape_with_playwright(
    seen: set[str],
    max_days_old: int | None = None,
    sde_exp_levels: list[int] | None = None,
    ai_exp_levels:  list[int] | None = None,
    on_progress: ProgressFn = None,
) -> list[dict]:
    """
    Two-phase scrape with pagination, early stopping, and parallel JD fetching.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright not installed.")
        return []

    def log(msg: str):
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    effective_max_days = MAX_DAYS_OLD if max_days_old is None else max_days_old
    effective_sde_exp  = SDE_EXPERIENCE_LEVELS if sde_exp_levels is None else sde_exp_levels
    effective_ai_exp   = AI_EXPERIENCE_LEVELS  if ai_exp_levels  is None else ai_exp_levels

    sde_kws = _effective_keywords(SDE_KEYWORDS, SDE_BOOST_KEYWORDS, effective_sde_exp)
    ai_kws  = _effective_keywords(AI_KEYWORDS,  AI_BOOST_KEYWORDS,  effective_ai_exp)

    # Build search plan: (keyword, location, experience_levels, category)
    search_plan = (
        [(kw, loc, effective_sde_exp, "sde") for kw in sde_kws for loc in SEARCH_LOCATIONS] +
        [(kw, loc, effective_ai_exp,  "ai")  for kw in ai_kws  for loc in SEARCH_LOCATIONS]
    )

    total_searches = len(search_plan)

    # ── Phase 1: Collect job cards ─────────────────────────────────────────────
    log(f"\n📡 Phase 1: Searching LinkedIn ({total_searches} queries)...\n")

    sde_candidates: list[dict] = []
    ai_candidates:  list[dict] = []
    all_ids: set[str] = set()    # for cross-query dedup

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()

        for search_idx, (keyword, location, exp_levels, category) in enumerate(search_plan, 1):
            # ── Early stopping (Optimization D) ────────────────────────────────
            if category == "sde" and len(sde_candidates) >= TARGET_SDE_JOBS * 2:
                log(f"  ⏭️  [{search_idx}/{total_searches}] Skipping \"{keyword}\" × \"{location}\" — SDE target met")
                continue
            if category == "ai" and len(ai_candidates) >= TARGET_AI_JOBS * 2:
                log(f"  ⏭️  [{search_idx}/{total_searches}] Skipping \"{keyword}\" × \"{location}\" — AI target met")
                continue
            if len(sde_candidates) + len(ai_candidates) >= MAX_JOBS_PER_RUN:
                log(f"  ⏭️  [{search_idx}/{total_searches}] Max candidates reached, stopping search")
                break

            days_label = f"≤{effective_max_days}d" if effective_max_days > 0 else "any age"
            log(f"  🔍 [{search_idx}/{total_searches}] \"{keyword}\" × \"{location}\" [exp={','.join(map(str, exp_levels))} {days_label}]")

            search_new = 0

            # ── Pagination (Optimization C) ────────────────────────────────────
            for page_num in range(MAX_PAGES_PER_SEARCH):
                start = page_num * CARDS_PER_PAGE
                url = _build_search_url(keyword, location, exp_levels, start)

                try:
                    page.goto(url, timeout=30_000)
                    page.wait_for_timeout(3500)
                except Exception as e:
                    log(f"     Page {page_num + 1}: ❌ load failed ({e})")
                    break

                new_cards = _parse_cards(page, seen, all_ids, keyword, location, effective_max_days)

                # Track IDs for dedup across searches
                for c in new_cards:
                    if len(sde_candidates) + len(ai_candidates) >= MAX_JOBS_PER_RUN:
                        break
                    all_ids.add(c["job_id"])
                    seen.add(c["job_id"])
                    c["category"] = category
                    if category == "sde":
                        sde_candidates.append(c)
                    else:
                        ai_candidates.append(c)

                total_cards = len(page.query_selector_all(".job-search-card"))
                search_new += len(new_cards)
                log(f"     Page {page_num + 1}: {total_cards} cards, {len(new_cards)} new")

                if len(sde_candidates) + len(ai_candidates) >= MAX_JOBS_PER_RUN:
                    break

                # Stop paginating if this page had few results (likely last page)
                if total_cards < CARDS_PER_PAGE * 0.8:
                    break

                _safe_sleep(1.5, 1.0)

            log(f"     ✅ {search_new} new jobs from this search\n")

        # ── Phase 1 Summary ────────────────────────────────────────────────────
        total_candidates = len(sde_candidates) + len(ai_candidates)
        sde_status = "✅" if len(sde_candidates) >= TARGET_SDE_JOBS else "⚠️"
        ai_status  = "✅" if len(ai_candidates) >= TARGET_AI_JOBS else "⚠️"

        log(f"📋 Phase 1 complete: {total_candidates} new jobs found")
        log(f"   SDE: {len(sde_candidates)} (need {TARGET_SDE_JOBS}) {sde_status}")
        log(f"   AI:  {len(ai_candidates)} (need {TARGET_AI_JOBS}) {ai_status}")

        # Select final candidates (trim to target)
        selected_sde = sde_candidates[:TARGET_SDE_JOBS]
        selected_ai  = ai_candidates[:TARGET_AI_JOBS]
        candidates   = selected_sde + selected_ai

        if not candidates:
            browser.close()
            return []

        log(f"   Selected: {len(candidates)} jobs for JD fetching\n")

        # ── Phase 2: Fetch JDs ──────────────────────────────────────────────────
        log(f"📄 Phase 2: Fetching JDs ({len(candidates)} jobs)...\n")

        # Sort newest-first before fetching
        candidates.sort(key=lambda c: c["days_old"] if c["days_old"] >= 0 else 999)

        jobs: list[dict] = []

        for i, c in enumerate(candidates, 1):
            job = _fetch_jd(page, c)
            jobs.append(job)
            jd_len = len(job.get("description", ""))
            age_label = f"{c['days_old']}d ago" if c["days_old"] >= 0 else "?"
            status = "✅" if jd_len > 100 else "⚠️ short JD"
            log(f"  [{i}/{len(candidates)}] {status} {c['title']} @ {c['company']} ({age_label}, {jd_len} chars)")

        browser.close()

    log(f"\n✅ Phase 2 complete: {len(jobs)} jobs with JDs fetched\n")
    return jobs


# ── Category Splitter ──────────────────────────────────────────────────────────

def _split_by_category(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split jobs into (sde_jobs, ai_jobs) using the 'category' field."""
    ai_kw_set = set(kw.lower() for kw in AI_KEYWORDS)
    sde, ai = [], []
    for j in jobs:
        cat = j.get("category")
        if cat == "ai" or (cat is None and j.get("keyword", "").lower() in ai_kw_set):
            ai.append(j)
        else:
            sde.append(j)
    return sde, ai


# ── Public Entry Point ─────────────────────────────────────────────────────────

def get_new_jobs(on_progress: ProgressFn = None) -> tuple[list[dict], set[str]]:
    """
    Return (new_jobs, updated_seen_set).
    Implements multi-stage fallback if configured.
    """
    seen = _load_seen(SEEN_JOBS_FILE)

    def log(msg: str):
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    log(f"Already seen {len(seen)} jobs. Searching for new ones…")

    # ── Primary scrape ─────────────────────────────────────────────────────────
    jobs = scrape_with_playwright(seen, on_progress=on_progress)
    sde, ai = _split_by_category(jobs)

    log(f"Primary result: {len(sde)} SDE (need {TARGET_SDE_JOBS}) + {len(ai)} AI (need {TARGET_AI_JOBS})")

    # ── Fallback stages (if configured) ────────────────────────────────────────
    for stage in FALLBACK_STAGES:
        sde_short = max(0, TARGET_SDE_JOBS - len(sde))
        ai_short  = max(0, TARGET_AI_JOBS  - len(ai))
        if sde_short == 0 and ai_short == 0:
            break

        label   = stage.get("label", "fallback")
        days    = stage.get("max_days_old", 0)
        sde_exp = stage.get("sde_experience_levels") or None
        ai_exp  = stage.get("ai_experience_levels")  or None

        log(f"⚠️  Fallback [{label}]: short {sde_short} SDE + {ai_short} AI")

        more = scrape_with_playwright(
            seen, max_days_old=days, sde_exp_levels=sde_exp,
            ai_exp_levels=ai_exp, on_progress=on_progress,
        )
        more_sde, more_ai = _split_by_category(more)
        sde += more_sde[:sde_short]
        ai  += more_ai[:ai_short]
        log(f"  After [{label}]: {len(sde)} SDE + {len(ai)} AI")

    # ── Final ──────────────────────────────────────────────────────────────────
    final = sde[:TARGET_SDE_JOBS] + ai[:TARGET_AI_JOBS]
    log(f"\n🎯 Final selection: {len(final)} jobs ({len(sde[:TARGET_SDE_JOBS])} SDE + {len(ai[:TARGET_AI_JOBS])} AI)")
    return final, seen
