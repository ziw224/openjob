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
    CATEGORY_CONFIGS,
    TARGET_JOBS,
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
    category_exp_overrides: dict[str, list[int]] | None = None,
    on_progress: ProgressFn = None,
) -> list[dict]:
    """
    Two-phase scrape with pagination, early stopping, and parallel JD fetching.
    Dynamically loops over all categories defined in config/search_config.json.
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
    overrides = category_exp_overrides or {}

    # Build search plan from all configured categories
    search_plan: list[tuple[str, str, list[int], str]] = []  # (keyword, location, exp_levels, cat_name)
    for cat_name, cat_cfg in CATEGORY_CONFIGS.items():
        exp_levels = overrides.get(cat_name) or cat_cfg.get("experience_levels", [2])
        keywords   = _effective_keywords(
            cat_cfg.get("keywords", []),
            cat_cfg.get("boost_keywords", []),
            exp_levels,
        )
        for kw in keywords:
            for loc in SEARCH_LOCATIONS:
                search_plan.append((kw, loc, exp_levels, cat_name))

    total_searches = len(search_plan)

    # ── Phase 1: Collect job cards ─────────────────────────────────────────────
    log(f"\n📡 Phase 1: Searching LinkedIn ({total_searches} queries)...\n")

    candidates_by_cat: dict[str, list[dict]] = {name: [] for name in CATEGORY_CONFIGS}
    all_ids: set[str] = set()
    total_collected = 0

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

        for search_idx, (keyword, location, exp_levels, cat_name) in enumerate(search_plan, 1):
            cat_cfg    = CATEGORY_CONFIGS[cat_name]
            cat_target = cat_cfg.get("target_count", 10)
            cat_list   = candidates_by_cat[cat_name]

            # ── Early stopping ──────────────────────────────────────────────────
            if len(cat_list) >= cat_target * 2:
                log(f"  ⏭️  [{search_idx}/{total_searches}] Skipping \"{keyword}\" — {cat_name} target met")
                continue
            if total_collected >= MAX_JOBS_PER_RUN:
                log(f"  ⏭️  [{search_idx}/{total_searches}] Max candidates reached, stopping")
                break

            days_label = f"≤{effective_max_days}d" if effective_max_days > 0 else "any age"
            log(f"  🔍 [{search_idx}/{total_searches}] [{cat_name}] \"{keyword}\" × \"{location}\" [exp={','.join(map(str, exp_levels))} {days_label}]")

            search_new = 0

            for page_num in range(MAX_PAGES_PER_SEARCH):
                start = page_num * CARDS_PER_PAGE
                url   = _build_search_url(keyword, location, exp_levels, start)

                try:
                    page.goto(url, timeout=30_000)
                    page.wait_for_timeout(3500)
                except Exception as e:
                    log(f"     Page {page_num + 1}: ❌ load failed ({e})")
                    break

                new_cards = _parse_cards(page, seen, all_ids, keyword, location, effective_max_days)

                for c in new_cards:
                    if total_collected >= MAX_JOBS_PER_RUN:
                        break
                    all_ids.add(c["job_id"])
                    seen.add(c["job_id"])
                    c["category"] = cat_name
                    cat_list.append(c)
                    total_collected += 1

                total_cards = len(page.query_selector_all(".job-search-card"))
                search_new += len(new_cards)
                log(f"     Page {page_num + 1}: {total_cards} cards, {len(new_cards)} new")

                if total_collected >= MAX_JOBS_PER_RUN:
                    break
                if total_cards < CARDS_PER_PAGE * 0.8:
                    break

                _safe_sleep(1.5, 1.0)

            log(f"     ✅ {search_new} new jobs from this search\n")

        # ── Phase 1 Summary ────────────────────────────────────────────────────
        log(f"📋 Phase 1 complete: {total_collected} new jobs found")
        for cat_name, cat_cfg in CATEGORY_CONFIGS.items():
            target = cat_cfg.get("target_count", 10)
            found  = len(candidates_by_cat[cat_name])
            status = "✅" if found >= target else "⚠️"
            log(f"   {cat_name}: {found} (need {target}) {status}")

        # Trim each category to target and combine
        candidates: list[dict] = []
        for cat_name, cat_cfg in CATEGORY_CONFIGS.items():
            target = cat_cfg.get("target_count", 10)
            candidates.extend(candidates_by_cat[cat_name][:target])

        if not candidates:
            browser.close()
            return []

        log(f"   Selected: {len(candidates)} jobs for JD fetching\n")

        # ── Phase 2: Fetch JDs ──────────────────────────────────────────────────
        log(f"📄 Phase 2: Fetching JDs ({len(candidates)} jobs)...\n")
        candidates.sort(key=lambda c: c["days_old"] if c["days_old"] >= 0 else 999)

        jobs: list[dict] = []
        for i, c in enumerate(candidates, 1):
            job = _fetch_jd(page, c)
            jobs.append(job)
            jd_len    = len(job.get("description", ""))
            age_label = f"{c['days_old']}d ago" if c["days_old"] >= 0 else "?"
            status    = "✅" if jd_len > 100 else "⚠️ short JD"
            log(f"  [{i}/{len(candidates)}] {status} {c['title']} @ {c['company']} ({age_label}, {jd_len} chars)")

        browser.close()

    log(f"\n✅ Phase 2 complete: {len(jobs)} jobs with JDs fetched\n")
    return jobs


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

    # Count results per category
    counts_by_cat = {name: sum(1 for j in jobs if j.get("category") == name)
                     for name in CATEGORY_CONFIGS}
    summary = ", ".join(f"{n}: {c}" for n, c in counts_by_cat.items())
    log(f"Primary result: {len(jobs)} jobs ({summary})")

    # ── Fallback stages (if configured) ────────────────────────────────────────
    for stage in FALLBACK_STAGES:
        targets    = {n: cfg.get("target_count", 10) for n, cfg in CATEGORY_CONFIGS.items()}
        shortfalls = {n: max(0, targets[n] - counts_by_cat.get(n, 0)) for n in targets}
        if all(v == 0 for v in shortfalls.values()):
            break

        label    = stage.get("label", "fallback")
        days     = stage.get("max_days_old", 0)
        exp_ovrd = stage.get("category_exp_overrides") or {}

        log(f"⚠️  Fallback [{label}]: {shortfalls}")
        more = scrape_with_playwright(
            seen, max_days_old=days,
            category_exp_overrides=exp_ovrd,
            on_progress=on_progress,
        )
        for j in more:
            cat = j.get("category", "primary")
            if shortfalls.get(cat, 0) > 0:
                jobs.append(j)
                counts_by_cat[cat] = counts_by_cat.get(cat, 0) + 1
                shortfalls[cat]   -= 1

    # ── Final trim ─────────────────────────────────────────────────────────────
    final: list[dict] = []
    for cat_name, cat_cfg in CATEGORY_CONFIGS.items():
        target   = cat_cfg.get("target_count", 10)
        cat_jobs = [j for j in jobs if j.get("category") == cat_name]
        final.extend(cat_jobs[:target])

    summary = ", ".join(
        f"{n}: {sum(1 for j in final if j.get('category') == n)}"
        for n in CATEGORY_CONFIGS
    )
    log(f"\n🎯 Final selection: {len(final)} jobs ({summary})")
    return final, seen
