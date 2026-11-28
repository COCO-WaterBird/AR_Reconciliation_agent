#!/usr/bin/env python
"""
Capture screenshots of the running review application (docs/images/ui/).

    streamlit run app/review_app.py --server.headless true --server.port 8501
    python scripts/capture_ui_screenshots.py [--url http://localhost:8501]

Drives the real workflow with the synthetic sample data through the UI
(clicks, not shortcuts): import, run, review results, investigate, attach
sample documents, re-investigate, controls, reports, audit, settings.
Requires the optional 'playwright' package and an installed Chrome.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "ui"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--width", type=int, default=1512)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": args.width, "height": 960}, device_scale_factor=1)
        tall = 2300

        def settle(ms: int = 2500) -> None:
            page.wait_for_timeout(ms)
            page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=120000)

        def nav(name: str) -> None:
            page.locator("[data-testid='stSidebarNav'] a", has_text=name).first.click()
            settle()

        def shot(name: str, full: bool = True) -> None:
            # Streamlit scrolls inside its main container, so a tall viewport
            # is what captures the whole page.
            if full:
                height = page.evaluate(
                    "() => (document.querySelector('[data-testid=\"stMain\"]') || document.body).scrollHeight")
                page.set_viewport_size({"width": args.width, "height": min(max(int(height) + 40, 960), 4200)})
                page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / f"after_{name}.png"), full_page=False)
            page.set_viewport_size({"width": args.width, "height": 960})
            print("captured", name)

        try:
            run_flow(page, args, settle, nav, shot)
        except Exception:
            page.screenshot(path=str(OUT / "capture_error.png"))
            raise
        browser.close()
    return 0


def run_flow(page, args, settle, nav, shot) -> None:
    if True:
        page.goto(args.url, wait_until="networkidle")
        settle(4000)
        shot("00_overview_empty", full=False)

        nav("Settings")
        page.locator("input[aria-label='Reviewer name']").fill("A. Analyst")
        page.keyboard.press("Enter")
        settle()

        nav("Data Import")
        shot("01_data_import")
        page.get_by_role("button", name="Run Reconciliation").click()
        settle(9000)
        shot("02_overview")

        nav("Reconciliation")
        shot("03_reconciliation", full=False)

        nav("Exception Workbench")
        page.get_by_role("button", name="Analyze All Pending").click()
        settle(12000)
        shot("04_workbench")

        nav("Exception Investigation")
        page.get_by_role("combobox").first.click()
        page.keyboard.type("BTRX-000168")
        page.keyboard.press("Enter")
        settle()
        shot("05_investigation_ai")
        page.get_by_role("tab", name="Financial Validation & Decision").click()
        settle(1500)
        shot("06_policy_decision")
        page.get_by_role("tab", name="Human Review").click()
        settle(1500)
        shot("07_human_review_blocked", full=False)

        nav("Evidence Center")
        page.get_by_role("button", name="Load Sample Documents").click()
        settle(5000)
        shot("08_evidence_center")

        nav("Exception Investigation")
        page.get_by_role("combobox").first.click()
        page.keyboard.type("BTRX-000155")
        page.keyboard.press("Enter")
        settle()
        page.get_by_role("button", name="Re-run Investigation").click()
        settle(9000)
        page.wait_for_timeout(3000)
        shot("09_investigation_evidence_ai")
        page.get_by_role("tab", name="Supporting Evidence").click()
        settle(1500)
        shot("10_supporting_evidence")
        page.get_by_role("tab", name="Financial Validation & Decision").click()
        settle(1500)
        shot("11_policy_eligible")

        for name, label in (("12_controls", "Financial Controls"), ("13_reports", "Reports"),
                            ("14_audit", "Audit History"), ("15_settings", "Settings")):
            nav(label)
            shot(name)


if __name__ == "__main__":
    sys.exit(main())
