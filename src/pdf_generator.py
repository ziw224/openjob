"""
src/pdf_generator.py – Convert a tailored HTML resume to PDF using Playwright.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def html_to_pdf(html_path: Path, pdf_name: str | None = None) -> Path | None:
    """
    Render *html_path* in headless Chromium and save a PDF.
    pdf_name: filename without extension, e.g. 'Resume-Stripe'.
    Returns the PDF path, or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    if pdf_name:
        pdf_path = html_path.parent / f"{pdf_name}.pdf"
    else:
        pdf_path = html_path.with_suffix(".pdf")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 816, "height": 1056})  # 8.5x11 @ 96dpi
            page.goto(f"file://{html_path.resolve()}", wait_until="networkidle")
            page.emulate_media(media="print")

            # Measure content; inject CSS transform to shrink to exactly one page
            content_height = page.evaluate("document.body.scrollHeight")
            page_height_px = 1056  # 11in @ 96dpi

            if content_height > page_height_px:
                scale = round((page_height_px / content_height) * 0.98, 4)
                logger.info(f"  Content {content_height}px > page → CSS scale {scale:.3f}")
                page.evaluate(f"""
                    document.body.style.transformOrigin = 'top left';
                    document.body.style.transform = 'scale({scale})';
                    document.body.style.width = '{round(100/scale, 2)}%';
                    document.body.style.overflow = 'visible';
                """)

            page.pdf(
                path=str(pdf_path),
                format="Letter",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                page_ranges="1",   # hard cap: never emit page 2
            )
            browser.close()
        logger.info(f"  ✅ PDF saved → {pdf_path.name}")
        return pdf_path
    except Exception as e:
        logger.error(f"  PDF generation failed for {html_path.name}: {e}")
        return None
