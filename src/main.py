"""
src/main.py – Daily job-hunt workflow orchestrator.

Phases:
  1. Scrape LinkedIn for new jobs
  2. Generate tailored resume + cover letter in parallel
  3. Save PDF + files locally in resume/output/YYYY-MM-DD/{Company}/
  4. Send Discord notification (optional)
"""
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
SRC_DIR      = PROJECT_ROOT / "src"
for p in (str(PROJECT_ROOT), str(SRC_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from datetime import date as _date
import os
from config.settings import OUTPUT_DIR, SEEN_JOBS_FILE, TARGET_JOBS, JOB_WORKERS, MAX_DAYS_OLD
from linkedin_scraper import get_new_jobs, _save_seen
from resume_tailor import tailor_resume
from pdf_generator import html_to_pdf
from cover_letter import generate_cover_letter
from notifier import send_discord_report

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "workflow.log"),
    ],
)
logger = logging.getLogger(__name__)


def process_job(job: dict, today_dir: Path) -> dict:
    """Full pipeline for one job: tailor resume + cover letter → PDF."""
    company_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", job["company"])
    company_dir  = today_dir / company_slug
    company_dir.mkdir(parents=True, exist_ok=True)

    label = f"{job['title']} @ {job['company']}"
    logger.info(f"▶ Starting: {label}")

    html_path = None
    cl_paths  = {"cover_letter": None, "why_company": None}

    with ThreadPoolExecutor(max_workers=2) as inner:
        resume_future = inner.submit(tailor_resume, job, company_dir)
        cl_future     = inner.submit(generate_cover_letter, job, company_dir)
        html_path = resume_future.result()
        cl_paths  = cl_future.result()

    pdf_path = None
    if html_path:
        candidate_name = os.getenv("CANDIDATE_NAME", "Resume")
        pdf_path = html_to_pdf(html_path, pdf_name=f"{candidate_name}-Resume-{job['company']}")

    success = html_path is not None and pdf_path is not None
    logger.info(f"{'✅' if success else '❌'} Done: {label}")
    if success:
        logger.info(f"   📂 Output: {company_dir}")

    return {
        "job":          job,
        "html_path":    html_path,
        "pdf_path":     pdf_path,
        "cover_letter": cl_paths.get("cover_letter"),
        "why_company":  cl_paths.get("why_company"),
        "success":      success,
    }


def run():
    import time
    t_start = time.time()

    llm_mode = os.getenv("LLM_MODE", "openai").strip().lower()
    workers  = 1 if llm_mode == "openclaw" else JOB_WORKERS

    logger.info("=" * 60)
    logger.info("openjob — Daily Job-Hunt Workflow")
    logger.info(f"Target: {TARGET_JOBS} jobs total")
    logger.info(f"Workers: {workers} | LLM: {llm_mode}")
    logger.info("=" * 60)

    today_dir = OUTPUT_DIR / _date.today().isoformat()
    today_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Scrape
    logger.info("\n📡 Scraping LinkedIn…")
    selected, seen = get_new_jobs(on_progress=lambda msg: print(msg, flush=True))

    if not selected:
        logger.info("No new jobs found today.")
        send_discord_report([])
        return

    logger.info(f"Found {len(selected)} new jobs")

    # Save job manifest
    jobs_log_path = PROJECT_ROOT / "data" / f"jobs_{_date.today().isoformat()}.json"
    jobs_log_path.parent.mkdir(exist_ok=True)
    existing = {}
    if jobs_log_path.exists():
        try:
            for j in json.loads(jobs_log_path.read_text()):
                existing[j["url"]] = j
        except Exception:
            pass
    for j in selected:
        existing[j["url"]] = {k: j[k] for k in ("title","company","location","url","category","job_id") if k in j}
    jobs_log_path.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=2))

    # Phase 2: Process jobs in parallel
    logger.info(f"\n⚙️  Generating resumes ({workers} workers)…")
    results = [None] * len(selected)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {pool.submit(process_job, job, today_dir): i for i, job in enumerate(selected)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                job = selected[idx]
                logger.error(f"  ❌ {job['title']} @ {job['company']}: {e}")
                results[idx] = {"job": job, "html_path": None, "pdf_path": None,
                                "cover_letter": None, "why_company": None, "success": False}

    _save_seen(SEEN_JOBS_FILE, seen)
    send_discord_report(results)

    elapsed = int(time.time() - t_start)
    ok = sum(r["success"] for r in results)
    logger.info(f"\n✅ Done in {elapsed//60}m {elapsed%60}s — {ok}/{len(results)} jobs")
    logger.info(f"   Output: {today_dir}")


if __name__ == "__main__":
    run()
