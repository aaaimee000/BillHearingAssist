"""
Floor System Scraper Plugin
============================
Currently configured for congress.gov as practice target.
Swap ACTIVE_CONFIG to FLOOR_SYSTEM_CONFIG on Monday.
"""

import asyncio
import os
from pathlib import Path
from .base import BasePlugin


CONGRESS_GOV_CONFIG = {
    "name": "congress.gov (practice)",
    "requires_login": False,
    "login": {},
    "bill_url_template": "https://www.congress.gov/bill/118th-congress/senate-bill/{bill_id}/text",
    "selectors": {
        "pdf_links": "a[href*='.pdf'], a[href*='download']",
        "page_ready_indicator": "h1",
        "testimony_section": None,
    },
    "timeouts": {
        "page_load": 15000,
        "element": 10000,
        "download": 30000,
    },
}

FLOOR_SYSTEM_CONFIG = {
    "name": "MGA Senate Floor System",
    "requires_login": True,
    "is_spa": False,
    "login": {
        "url": "https://senatefloor/floor/Accounts/Login?ReturnUrl=%2ffloor",
        "username_selector": "#Username",
        "password_selector": "#Password",
        "submit_selector": "button[type='submit']",
        "post_login_wait": "networkidle",
    },
    "bill_url_template": "https://senatefloor/floor/bills/{bill_id}/testimony",
    "selectors": {
        "pdf_links": "a[href$='.pdf']",
        "page_ready_indicator": "main",
        "testimony_section": None,
    },
    "timeouts": {
        "page_load": 20000,
        "element": 15000,
        "download": 60000,
    },
    "debug_headless": False,
}

# ← Change this to FLOOR_SYSTEM_CONFIG on Monday
# ACTIVE_CONFIG = CONGRESS_GOV_CONFIG
ACTIVE_CONFIG = FLOOR_SYSTEM_CONFIG


class FloorScraperPlugin(BasePlugin):
    name = "scraper"
    description = "Downloads testimony PDFs from the legislative floor system"

    def input_schema(self):
        return {
            "bill_id": "string — the bill number to look up",
        }

    async def run(self, inputs: dict) -> dict:
        bill_id = inputs.get("bill_id", "").strip()
        if not bill_id:
            return {"error": "bill_id is required", "downloaded_files": [], "count": 0}

        config = ACTIVE_CONFIG
        username = os.getenv("FLOOR_SYSTEM_USERNAME", "")
        password = os.getenv("FLOOR_SYSTEM_PASSWORD", "")
        output_dir = f"storage/downloads/{bill_id}"
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
                "downloaded_files": [],
                "count": 0,
            }

        downloaded_files = []
        errors = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=not config.get("debug_headless", False),
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = await context.new_page()
            timeouts = config.get("timeouts", {})

            # Step 1: Login if needed
            if config.get("requires_login"):
                login_cfg = config["login"]
                await page.goto(login_cfg["url"])
                await page.wait_for_load_state("networkidle", timeout=timeouts.get("page_load", 15000))
                await page.fill(login_cfg["username_selector"], username)
                await page.fill(login_cfg["password_selector"], password)
                await asyncio.sleep(0.8)
                await page.click(login_cfg["submit_selector"])
                wait_for = login_cfg.get("post_login_wait", "networkidle")
                if wait_for == "networkidle":
                    await page.wait_for_load_state("networkidle", timeout=timeouts.get("page_load", 15000))
                else:
                    await page.wait_for_selector(wait_for, timeout=timeouts.get("element", 10000))

            # Step 2: Navigate to bill page
            bill_url = config["bill_url_template"].format(bill_id=bill_id)
            await page.goto(bill_url)

            ready_selector = config["selectors"].get("page_ready_indicator")
            if ready_selector:
                try:
                    await page.wait_for_selector(ready_selector, timeout=timeouts.get("element", 10000))
                except Exception:
                    await page.wait_for_load_state("domcontentloaded")
            else:
                await page.wait_for_load_state("networkidle", timeout=timeouts.get("page_load", 15000))

            testimony_section = config["selectors"].get("testimony_section")
            if testimony_section:
                try:
                    await page.click(testimony_section)
                    await page.wait_for_load_state("networkidle")
                except Exception:
                    pass

            # Step 3: Find PDF links
            pdf_selector = config["selectors"]["pdf_links"]
            links = await page.query_selector_all(pdf_selector)

            # Collect metadata about found documents (for the frontend to display)
            documents_found = []
            for link in links:
                href = await link.get_attribute("href") or ""
                text = (await link.inner_text()).strip()
                if href:
                    documents_found.append({"name": text or href.split("/")[-1], "url": href})

            # Step 4: Download each document
            for i, link in enumerate(links):
                href = await link.get_attribute("href") or ""
                link_text = (await link.inner_text()).strip()
                if not href:
                    continue
                if href.startswith("/"):
                    base_url = "/".join(bill_url.split("/")[:3])
                    href = base_url + href

                try:
                    response = await page.request.get(href, timeout=timeouts.get("download", 30000))
                    if response.ok:
                        safe_name = "".join(
                            c if c.isalnum() or c in "._- " else "_" for c in link_text
                        )[:60]
                        filename = f"{i+1:02d}_{safe_name or href.split('/')[-1]}"
                        if not filename.endswith(".pdf"):
                            filename += ".pdf"
                        file_path = Path(output_dir) / filename
                        file_path.write_bytes(await response.body())
                        downloaded_files.append(str(file_path))
                    else:
                        async with page.expect_download(timeout=timeouts.get("download", 30000)) as dl_info:
                            await link.click()
                        download = await dl_info.value
                        file_path = Path(output_dir) / download.suggested_filename
                        await download.save_as(str(file_path))
                        downloaded_files.append(str(file_path))
                except Exception as e:
                    errors.append(f"Failed to download {href}: {str(e)}")

            await browser.close()

        return {
            "downloaded_files": downloaded_files,
            "documents_found": documents_found,
            "errors": errors,
            "count": len(downloaded_files),
            "bill_id": bill_id,
        }
