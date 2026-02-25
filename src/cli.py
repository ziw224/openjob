#!/usr/bin/env python3
"""
src/cli.py – openjob CLI

Commands:
  run                   Run full pipeline (scrape + tailor + PDF)
  retry <url>           Re-run pipeline for a single LinkedIn job URL
  retry-day [date]      Re-run all failed jobs for a date (default: today)
  status                Print today's output summary
  model <backend>       Switch LLM backend (openai | claude | codex)

Usage:
  openjob run
  openjob retry "https://www.linkedin.com/jobs/view/1234567890"
  openjob retry-day 2026-02-25
  openjob status
  openjob model openai
"""
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

(PROJECT_ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "logs" / "workflow.log"),
    ],
)


def cmd_run():
    import main
    main.run()


def cmd_status():
    from config.settings import OUTPUT_DIR
    today = date.today().isoformat()
    today_dir = OUTPUT_DIR / today
    result = {"date": today, "companies": [], "total": 0, "ok": 0}
    if today_dir.exists():
        for comp_dir in sorted(today_dir.iterdir()):
            if not comp_dir.is_dir():
                continue
            files = list(comp_dir.iterdir())
            has_pdf = any(f.suffix == ".pdf" for f in files)
            result["companies"].append({
                "name":    comp_dir.name,
                "success": has_pdf,
                "files":   [f.name for f in sorted(files)],
            })
        result["total"] = len(result["companies"])
        result["ok"]    = sum(1 for c in result["companies"] if c["success"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_model(target: str | None = None):
    env_path = PROJECT_ROOT / ".env"
    if not target:
        print("Usage: openjob model <openai|claude|codex>")
        return
    lines, seen = [], False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("LLM_MODE="):
                lines.append(f"LLM_MODE={target}")
                seen = True
            else:
                lines.append(line)
    if not seen:
        lines.append(f"LLM_MODE={target}")
    env_path.write_text("\n".join(lines).rstrip() + "\n")
    print(f"✅ LLM_MODE set to: {target}")


def _fetch_jd(url: str) -> dict:
    """Fetch job title, company, location, description from a LinkedIn URL."""
    from playwright.sync_api import sync_playwright
    job = {"url": url.split("?")[0], "title": "", "company": "", "location": "", "description": ""}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page    = browser.new_page()
        try:
            page.goto(url, timeout=30_000)
            page.wait_for_timeout(2000)
            for sel in [".show-more-less-html__markup", "#job-details", ".description__text"]:
                el = page.query_selector(sel)
                if el:
                    job["description"] = el.inner_text().strip()
                    break
            el = page.query_selector("h1")
            if el: job["title"] = el.inner_text().strip()
            el = page.query_selector(".topcard__org-name-link, .job-details-jobs-unified-top-card__company-name a")
            if el: job["company"] = el.inner_text().strip()
            el = page.query_selector(".topcard__flavor--bullet, .job-details-jobs-unified-top-card__bullet")
            if el: job["location"] = el.inner_text().strip()
        finally:
            browser.close()
    return job


def cmd_retry(url: str, title: str = "", company: str = "", location: str = "", category: str = "ai"):
    """Re-run pipeline for a single LinkedIn job URL."""
    from config.settings import OUTPUT_DIR
    from main import process_job

    logging.info(f"Fetching JD from: {url}")
    job = _fetch_jd(url)
    if title:    job["title"]    = title
    if company:  job["company"]  = company
    if location: job["location"] = location
    job["category"] = category
    job.setdefault("job_id", url.rstrip("/").split("/")[-1])

    if not job["title"]:
        logging.error("Could not extract job title. Try: openjob retry <url> --title '...' --company '...'")
        return

    logging.info(f"  {job['title']} @ {job['company']} | {job['location']}")

    today_dir = OUTPUT_DIR / date.today().isoformat()
    today_dir.mkdir(parents=True, exist_ok=True)

    result = process_job(job, today_dir)
    logging.info(f"{'✅' if result['success'] else '❌'} {job['title']} @ {job['company']}")

    if result["success"]:
        company_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", job["company"])
        logging.info(f"   📂 {today_dir / company_slug}")


def cmd_retry_day(day: str | None = None):
    """Re-run all failed jobs for a given date."""
    from config.settings import OUTPUT_DIR
    from main import process_job

    target_date   = day or date.today().isoformat()
    manifest_path = PROJECT_ROOT / "data" / f"jobs_{target_date}.json"

    if not manifest_path.exists():
        logging.error(f"No manifest for {target_date}: {manifest_path}")
        return

    jobs      = json.loads(manifest_path.read_text())
    today_dir = OUTPUT_DIR / target_date
    today_dir.mkdir(parents=True, exist_ok=True)

    # A job is done if its output dir has a PDF
    failed = []
    for job in jobs:
        company_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", job["company"])
        comp_dir     = today_dir / company_slug
        has_pdf      = comp_dir.exists() and any(f.suffix == ".pdf" for f in comp_dir.iterdir())
        if not has_pdf:
            failed.append(job)

    if not failed:
        logging.info(f"✅ All jobs for {target_date} already succeeded — nothing to retry.")
        return

    logging.info(f"Retrying {len(failed)} failed jobs for {target_date}…")

    results = []
    for job in failed:
        if not job.get("description"):
            try:
                fetched = _fetch_jd(job["url"])
                job["description"] = fetched.get("description", "")
            except Exception as e:
                logging.warning(f"JD fetch failed: {e}")
                job["description"] = ""
        result = process_job(job, today_dir)
        results.append(result)
        logging.info(f"  {'✅' if result['success'] else '❌'} {job['title']} @ {job['company']}")

    ok = sum(r["success"] for r in results)
    logging.info(f"\nDone — {ok}/{len(results)} succeeded")


COMMANDS = {
    "run":       cmd_run,
    "retry":     cmd_retry,
    "retry-day": cmd_retry_day,
    "status":    cmd_status,
    "model":     cmd_model,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: openjob [{' | '.join(COMMANDS)}]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "retry":
        if len(sys.argv) < 3:
            print("Usage: openjob retry <url> [--title ...] [--company ...] [--category ai|sde]")
            sys.exit(1)
        url = sys.argv[2]
        kwargs: dict = {}
        args, i = sys.argv[3:], 0
        while i < len(args):
            if args[i].startswith("--") and i + 1 < len(args):
                kwargs[args[i][2:]] = args[i + 1]; i += 2
            else:
                i += 1
        cmd_retry(url, **kwargs)
    elif cmd == "retry-day":
        cmd_retry_day(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "model":
        cmd_model(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        COMMANDS[cmd]()
