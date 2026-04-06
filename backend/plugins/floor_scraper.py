"""
Floor System Scraper Plugin
============================
Connects to the MGA Senate Floor System (requires login + MGA wifi).

WHAT THIS SCRAPER DOES:
  1. Logs in with stored credentials
  2. Navigates to the bill's Committee Testimony tab
  3. Reads the testimony table (Name, Organization, Position, PDF link)
  4. Downloads every PDF linked in the table
  5. Returns both the file paths AND the table metadata (who submitted what)
     so the memo generator can write "Admin from Maryland Just Power
     Alliance submitted testimony in FAVOR (FWA)..." rather than just
     dumping raw PDF text.

POSITION CODES (from the Floor System):
  FWA = For With Amendments
  FAV = In Favor
  UNF = Unfavorable / Opposed
  IMR = Information / Neutral
  (empty) = No written testimony or oral only

URL PATTERN DISCOVERED FROM SCREENSHOTS:
  Login:     https://senatefloor/floor/Accounts/Login?ReturnUrl=%2ffloor
  Bill page: https://senatefloor/floor
             e.g. https://senatefloor/floor
  The "Committee Testimony" tab appears in the bill nav — we click it.
"""

import asyncio
import os
from pathlib import Path
from .base import BasePlugin

# Position code → human readable label
POSITION_LABELS = {
    "FAV": "In Favor",
    "FWA": "In Favor with Amendments",
    "UNF": "Unfavorable / Opposed",
    "IMR": "Informational / Neutral",
    "":    "No position stated",
}

FLOOR_SYSTEM_CONFIG = {
    "name": "MGA Senate Floor System",
    "requires_login": True,
    "login": {
        "url": "https://senatefloor/floor/Accounts/Login?ReturnUrl=%2ffloor",
        "username_selector": "#Username",
        "password_selector": "#Password",
        "submit_selector":   "button[type='submit']",
        "post_login_wait":   "networkidle",
    },
    # Bill detail page — Committee Testimony tab is reached by clicking the tab
    # after landing on the bill's detail page
    # "bill_detail_url": "https://senatefloor/floor/Bills/Details/{bill_id}",
    "bill_detail_url": "https://senatefloor/floor", 
    # CSS selector for the Committee Testimony tab link in the bill nav
    # From screenshot: the last tab in the nav bar reads "Committee Testimony"
    "testimony_tab_selector": "a:has-text('Committee Testimony')",
    # The testimony table rows — each tr in tbody of the testimony table
    "testimony_row_selector": "table tbody tr",
    # Within each row, column positions (0-indexed):
    #   0 = Name, 1 = Organization, 2 = Position, 3 = Testimony (may have PDF link)
    "pdf_link_in_row_selector": "td:nth-child(4) a[href]",
    "timeouts": {
        "page_load": 20000,
        "element":   15000,
        "download":  60000,
    },
    "debug_headless": False,  # set True to watch browser during development
}


class FloorScraperPlugin(BasePlugin):
    name = "scraper"
    description = "Downloads written testimony PDFs from the MGA Senate Floor System (requires login)"

    def input_schema(self):
        return {
            "bill_id": "string — e.g. 'HB1532' or 'SB0100' (as shown on the Floor System)",
        }

    async def run(self, inputs: dict) -> dict:
        # Accept both 'bill_id' and 'bill_number' keys for backwards compatibility
        bill_id = (inputs.get("bill_id") or inputs.get("bill_number") or "").strip().upper()

        if not bill_id:
            return {
                "error": "bill_id is required (e.g. 'HB1532')",
                "downloaded_files": [], "documents_found": [], "count": 0,
            }

        username = os.getenv("FLOOR_SYSTEM_USERNAME", "")
        password = os.getenv("FLOOR_SYSTEM_PASSWORD", "")

        if not username or not password:
            return {
                "error": (
                    "FLOOR_SYSTEM_USERNAME and FLOOR_SYSTEM_PASSWORD must be set in your .env file. "
                    "These are your Senate Floor System login credentials."
                ),
                "downloaded_files": [], "documents_found": [], "count": 0,
            }

        output_dir = Path(f"storage/downloads/{bill_id}")
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
                "downloaded_files": [], "documents_found": [], "count": 0,
            }

        config = FLOOR_SYSTEM_CONFIG
        downloaded_files = []
        testimony_records = []   # rich metadata: name, org, position, filename
        errors = []

        headless = not config.get("debug_headless", False)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
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
            timeouts = config["timeouts"]

            # ── STEP 1: Login ─────────────────────────────────────────────
            print(f"[FloorScraper] Logging in as {username}...")
            await page.goto(config["login"]["url"])
            await page.wait_for_load_state("networkidle", timeout=timeouts["page_load"])

            await page.fill(config["login"]["username_selector"], username)
            await page.fill(config["login"]["password_selector"], password)
            await asyncio.sleep(0.6)
            await page.click(config["login"]["submit_selector"])
            await page.wait_for_load_state("networkidle", timeout=timeouts["page_load"])

            # Check login succeeded — if we're back on login page, credentials are wrong
            if "Login" in page.url or "login" in page.url.lower():
                await browser.close()
                return {
                    "error": (
                        "Login failed — incorrect credentials or the Floor System is unavailable. "
                        "Check FLOOR_SYSTEM_USERNAME and FLOOR_SYSTEM_PASSWORD in your .env file."
                    ),
                    "downloaded_files": [], "documents_found": [], "count": 0,
                }
            print(f"[FloorScraper] Logged in. Now at: {page.url}")

            # ── STEP 2: Navigate to bill detail page ──────────────────────
            bill_url = config["bill_detail_url"].format(bill_id=bill_id)
            print(f"[FloorScraper] Navigating to bill: {bill_url}")
            await page.goto(bill_url)
            await page.wait_for_load_state("networkidle", timeout=timeouts["page_load"])

            # ── STEP 3: Click the Committee Testimony tab ─────────────────
            print(f"[FloorScraper] Clicking Committee Testimony tab...")
            try:
                await page.click(config["testimony_tab_selector"], timeout=timeouts["element"])
                await page.wait_for_load_state("networkidle", timeout=timeouts["page_load"])
            except Exception as e:
                # Tab selector might differ — fall back to URL-based navigation
                print(f"[FloorScraper] Tab click failed ({e}), trying URL approach...")
                # Some Floor System versions use a direct URL for testimony tab
                testimony_url_fallbacks = [
                    f"https://senatefloor/floor/Bills/Details/{bill_id}#testimony",
                    f"https://senatefloor/floor/Bills/Testimony/{bill_id}",
                    f"https://senatefloor/floor/{bill_id}/testimony",
                ]
                for fallback in testimony_url_fallbacks:
                    try:
                        await page.goto(fallback)
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        # If the page has a table, we found it
                        table = await page.query_selector("table tbody tr")
                        if table:
                            print(f"[FloorScraper] Found testimony table at: {fallback}")
                            break
                    except Exception:
                        continue

            # ── STEP 4: Read testimony table rows ─────────────────────────
            # Each row: Name | Organization | Position | Testimony link | Committee
            print(f"[FloorScraper] Reading testimony table...")
            rows = await page.query_selector_all(config["testimony_row_selector"])
            print(f"[FloorScraper] Found {len(rows)} testimony entries")

            for i, row in enumerate(rows):
                cells = await row.query_selector_all("td")
                if len(cells) < 3:
                    continue

                # Extract cell text
                name     = (await cells[0].inner_text()).strip() if len(cells) > 0 else ""
                org      = (await cells[1].inner_text()).strip() if len(cells) > 1 else ""
                position = (await cells[2].inner_text()).strip() if len(cells) > 2 else ""

                # Look for a PDF link in the testimony column (col 4, index 3)
                pdf_link_el = await row.query_selector(config["pdf_link_in_row_selector"])
                pdf_href    = ""
                pdf_text    = ""
                if pdf_link_el:
                    pdf_href = (await pdf_link_el.get_attribute("href") or "").strip()
                    pdf_text = (await pdf_link_el.inner_text()).strip()

                # Build record — always record even if no PDF (oral testimony)
                record = {
                    "name":          name,
                    "organization":  org,
                    "position":      position,
                    "position_label": POSITION_LABELS.get(position, position),
                    "testimony_type": "Written" if pdf_href else "Oral",
                    "pdf_url":       pdf_href,
                    "pdf_filename":  "",  # filled in after download
                }
                testimony_records.append(record)

                # Download the PDF if there is one
                if pdf_href:
                    if pdf_href.startswith("/"):
                        pdf_href = "https://senatefloor" + pdf_href
                    elif not pdf_href.startswith("http"):
                        pdf_href = f"https://senatefloor/floor/{pdf_href}"

                    try:
                        response = await page.request.get(
                            pdf_href,
                            timeout=timeouts["download"]
                        )
                        if response.ok:
                            # Name the file: 001_LastFirst_Org.pdf
                            safe_name = "".join(
                                c if c.isalnum() or c in "._- " else "_"
                                for c in f"{name}_{org}"
                            )[:50]
                            filename  = f"{i+1:03d}_{safe_name}.pdf"
                            file_path = output_dir / filename
                            file_path.write_bytes(await response.body())
                            downloaded_files.append(str(file_path))
                            record["pdf_filename"] = filename
                            print(f"[FloorScraper]   Downloaded: {filename}")
                        else:
                            errors.append(f"HTTP {response.status} for {name}'s testimony")
                    except Exception as e:
                        errors.append(f"Failed to download {name}'s testimony: {str(e)}")

            await browser.close()

        print(f"[FloorScraper] Done: {len(downloaded_files)} PDFs, {len(errors)} errors")

        return {
            "downloaded_files":  downloaded_files,
            "documents_found":   [
                {"name": r["pdf_filename"] or f"{r['name']} (oral)", "url": r["pdf_url"]}
                for r in testimony_records if r["pdf_url"] or r["testimony_type"] == "Oral"
            ],
            "testimony_records": testimony_records,   # rich metadata for memo generator
            "errors":            errors,
            "count":             len(downloaded_files),
            "bill_id":           bill_id,
            "total_testifiers":  len(testimony_records),
        }
