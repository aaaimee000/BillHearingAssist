"""
Legislative Document Scraper
==============================
Practice target : congress.gov  (no login needed, has real bill PDFs)
Production target: https://senatefloor/floor  (swap config on Monday)

KEY DESIGN: All site-specific values live in SITE_CONFIG.
The scraper logic below never changes — only the config does.

HOW TO USE TODAY (practice):
  1. pip install playwright pymupdf
  2. playwright install chromium
  3. python floor_scraper.py --mode practice

HOW TO USE MONDAY (production):
  1. Open senatefloor/floor in Chrome, inspect the HTML
  2. Fill in FLOOR_SYSTEM_CONFIG selectors (see instructions below)
  3. python floor_scraper.py --mode production --bill-id HB1234
"""

import asyncio
import os
import sys
import argparse
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# SITE CONFIGURATIONS
# Each config is a plain dict. Swap them out without touching any logic below.
# ─────────────────────────────────────────────────────────────────────────────

# ── PRACTICE: congress.gov ────────────────────────────────────────────────────
# This is a real public site you can test against right now.
# It has actual bill pages with linked PDF documents.
CONGRESS_GOV_CONFIG = {
    "name": "congress.gov (practice)",
    "requires_login": False,
    "login": {},

    # A real bill page on congress.gov with committee documents
    # Change the URL to any bill you want to explore
    "bill_url_template": "https://www.congress.gov/bill/118th-congress/senate-bill/{bill_id}/text",

    # On monday: inspect the Floor System and find these selectors
    # For now these match congress.gov's HTML structure
    "selectors": {
        # CSS selector for all PDF download links on the page
        "pdf_links": "a[href*='.pdf'], a[href*='document'], a[href*='download']",
        # CSS selector that confirms the page has loaded (wait for this)
        "page_ready_indicator": "h1.legDetail",
        # Optional: if testimonies are in a specific tab or section
        "testimony_section": None,
    },

    # How long to wait for slow government servers (milliseconds)
    "timeouts": {
        "page_load": 15000,
        "element": 10000,
        "download": 30000,
    },
}

# ── PRODUCTION: MGA Senate Floor System ───────────────────────────────────────
# INSTRUCTIONS FOR MONDAY:
# Open senatefloor/floor in Chrome. Do these steps:
#
# STEP A — Find login selectors:
#   Right-click the username input → Inspect
#   Note the id or name attribute, e.g. id="username" → selector is "#username"
#   Do the same for password field and submit button
#
# STEP B — Find page_ready_indicator:
#   After logging in, right-click any element that only appears when logged in
#   Inspect it, note its CSS selector — this is what Playwright waits for
#
# STEP C — Find PDF link selector:
#   On a bill's testimony page, right-click a PDF download link → Inspect
#   Look at the <a> tag's attributes. Common patterns:
#     href ends with .pdf        → selector: "a[href$='.pdf']"
#     has class "download-link"  → selector: "a.download-link"
#     inside a table             → selector: "table.testimony-list a"
#
# STEP D — Check if it's a SPA:
#   If after clicking a bill the URL doesn't change but content does = SPA
#   Set "is_spa" to True — Playwright handles this but needs different waits
#
FLOOR_SYSTEM_CONFIG = {
    "name": "MGA Senate Floor System",
    "requires_login": True,
    "is_spa": False,  # UPDATE THIS: True if it's a React/Angular app

    "login": {
        "url": "https://senatefloor/floor/login",     # UPDATE: actual login page URL
        "username_selector": "#username",              # UPDATE: inspect the login form
        "password_selector": "#password",             # UPDATE: inspect the login form
        "submit_selector": "button[type='submit']",   # UPDATE: inspect the submit button
        "post_login_wait": "networkidle",             # or a specific selector string
    },

    # URL pattern for a specific bill's testimony page
    # {bill_id} gets replaced with the actual bill number
    "bill_url_template": "https://senatefloor/floor/bills/{bill_id}/testimony",

    "selectors": {
        # UPDATE on Monday after inspecting the testimony page HTML:
        "pdf_links": "a[href$='.pdf']",        # most common pattern
        "page_ready_indicator": "main",         # something always present after load
        "testimony_section": None,              # e.g. "#testimony-tab" if behind a tab
    },

    "timeouts": {
        "page_load": 20000,   # government servers are slow, be generous
        "element": 15000,
        "download": 60000,    # PDFs can be large
    },

    # Some government sites block headless browsers.
    # If scraping fails, set this to True to run a visible browser (debugging only)
    "debug_headless": False,
}


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER LOGIC  (never needs to change — only the config above changes)
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_testimonies(
    config: dict,
    bill_id: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    output_dir: str = "storage/downloads",
) -> dict:
    """
    Main scraper function. Works for any site given the right config.
    Returns a dict with downloaded file paths and metadata.
    """
    from playwright.async_api import async_playwright

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    downloaded_files = []
    errors = []

    headless = not config.get("debug_headless", False)
    print(f"\n[Scraper] Starting: {config['name']}")
    print(f"[Scraper] Bill ID: {bill_id}")
    print(f"[Scraper] Headless: {headless}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
            # ^ Helps avoid basic bot detection on some sites
        )

        # Create context with a realistic user agent
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            # Tell the browser where to save downloads
            accept_downloads=True,
        )

        page = await context.new_page()
        timeouts = config.get("timeouts", {})

        # ── STEP 1: Login (if required) ───────────────────────────────────────
        if config.get("requires_login"):
            login_cfg = config["login"]
            print(f"[Scraper] Logging in at: {login_cfg['url']}")

            await page.goto(login_cfg["url"])
            await page.wait_for_load_state("networkidle",
                                           timeout=timeouts.get("page_load", 15000))

            # Fill credentials
            await page.fill(login_cfg["username_selector"], username or "")
            await page.fill(login_cfg["password_selector"], password or "")

            # Small human-like delay before clicking (avoids some bot detection)
            await asyncio.sleep(0.8)
            await page.click(login_cfg["submit_selector"])

            # Wait for login to complete
            wait_for = login_cfg.get("post_login_wait", "networkidle")
            if wait_for == "networkidle":
                await page.wait_for_load_state("networkidle",
                                               timeout=timeouts.get("page_load", 15000))
            else:
                # If it's a CSS selector, wait for that element to appear
                await page.wait_for_selector(wait_for,
                                             timeout=timeouts.get("element", 10000))

            print("[Scraper] Login successful")

        # ── STEP 2: Navigate to bill testimony page ───────────────────────────
        bill_url = config["bill_url_template"].format(bill_id=bill_id)
        print(f"[Scraper] Navigating to: {bill_url}")
        await page.goto(bill_url)

        # Wait for the page to be ready
        ready_selector = config["selectors"].get("page_ready_indicator")
        if ready_selector:
            try:
                await page.wait_for_selector(ready_selector,
                                             timeout=timeouts.get("element", 10000))
            except Exception as e:
                print(f"[Scraper] Warning: page ready indicator not found: {e}")
                # Don't fail — the content might still be there
                await page.wait_for_load_state("domcontentloaded")
        else:
            await page.wait_for_load_state("networkidle",
                                           timeout=timeouts.get("page_load", 15000))

        # ── STEP 2b: Click testimony tab/section if needed ────────────────────
        testimony_section = config["selectors"].get("testimony_section")
        if testimony_section:
            try:
                await page.click(testimony_section)
                await page.wait_for_load_state("networkidle")
                print(f"[Scraper] Clicked testimony section: {testimony_section}")
            except Exception as e:
                print(f"[Scraper] Could not click testimony section: {e}")

        # ── STEP 3: Find all PDF links ────────────────────────────────────────
        pdf_selector = config["selectors"]["pdf_links"]
        print(f"[Scraper] Looking for PDF links with selector: {pdf_selector}")

        links = await page.query_selector_all(pdf_selector)
        print(f"[Scraper] Found {len(links)} potential document links")

        if len(links) == 0:
            # Diagnostic help: dump all links on the page so we can find the right selector
            print("\n[Scraper] DEBUG: No links found. Here are ALL links on the page:")
            all_links = await page.query_selector_all("a[href]")
            for link in all_links[:30]:  # show first 30
                href = await link.get_attribute("href")
                text = await link.inner_text()
                print(f"  text='{text.strip()[:50]}' href='{href}'")
            print("\n[Scraper] Use the above to find the right selector for pdf_links config")

        # ── STEP 4: Download each document ────────────────────────────────────
        for i, link in enumerate(links):
            href = await link.get_attribute("href")
            link_text = (await link.inner_text()).strip()

            if not href:
                continue

            # Make relative URLs absolute
            if href.startswith("/"):
                base_url = "/".join(bill_url.split("/")[:3])
                href = base_url + href

            print(f"[Scraper] Downloading ({i+1}/{len(links)}): {link_text[:50]} — {href}")

            try:
                # Method A: Direct HTTP download (works for simple PDF links)
                # Playwright's request object shares the authenticated session
                response = await page.request.get(
                    href,
                    timeout=timeouts.get("download", 30000)
                )

                if response.ok:
                    # Use link text as filename, fall back to URL filename
                    safe_name = "".join(c if c.isalnum() or c in "._- " else "_"
                                        for c in link_text)[:60]
                    filename = f"{i+1:02d}_{safe_name or href.split('/')[-1]}"
                    if not filename.endswith(".pdf"):
                        filename += ".pdf"

                    file_path = Path(output_dir) / filename
                    file_path.write_bytes(await response.body())
                    downloaded_files.append(str(file_path))
                    print(f"[Scraper]   Saved: {file_path}")

                else:
                    # Method B: Click the link and let Playwright handle the download
                    # Use this when Method A gets redirected or blocked
                    print(f"[Scraper]   Direct download failed (status {response.status}), trying click...")
                    async with page.expect_download(timeout=timeouts.get("download", 30000)) as dl_info:
                        await link.click()
                    download = await dl_info.value
                    file_path = Path(output_dir) / download.suggested_filename
                    await download.save_as(str(file_path))
                    downloaded_files.append(str(file_path))
                    print(f"[Scraper]   Saved via click: {file_path}")

            except Exception as e:
                error_msg = f"Failed to download {href}: {e}"
                errors.append(error_msg)
                print(f"[Scraper]   ERROR: {error_msg}")

        await browser.close()

    print(f"\n[Scraper] Complete: {len(downloaded_files)} downloaded, {len(errors)} errors")
    return {
        "downloaded_files": downloaded_files,
        "errors": errors,
        "count": len(downloaded_files),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSIS TOOL
# Run this first on Monday to understand the Floor System's HTML structure
# ─────────────────────────────────────────────────────────────────────────────

async def diagnose_site(config: dict, username: str, password: str):
    """
    Logs in and dumps useful info about the page structure.
    Run this before scraping to find the right selectors.
    """
    from playwright.async_playwright import async_playwright

    print(f"\n[Diagnose] Running site diagnosis for: {config['name']}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Always visible for diagnosis
        page = await browser.new_page()

        if config.get("requires_login"):
            login_cfg = config["login"]
            await page.goto(login_cfg["url"])
            await page.wait_for_load_state("networkidle")

            # Detect what type of login form this is
            has_form = await page.query_selector("form")
            has_saml = "saml" in page.url.lower() or "sso" in page.url.lower()
            print(f"\n[Diagnose] Login page type:")
            print(f"  Has <form>: {bool(has_form)}")
            print(f"  Looks like SSO/SAML: {has_saml}")
            print(f"  Page URL: {page.url}")

            # List all input fields on the login page
            inputs = await page.query_selector_all("input")
            print(f"\n[Diagnose] Input fields on login page:")
            for inp in inputs:
                id_ = await inp.get_attribute("id")
                name = await inp.get_attribute("name")
                type_ = await inp.get_attribute("type")
                print(f"  <input type='{type_}' id='{id_}' name='{name}'>")
                print(f"    → selector: #{id_}" if id_ else f"    → selector: [name='{name}']")

            await page.fill(login_cfg["username_selector"], username)
            await page.fill(login_cfg["password_selector"], password)
            await page.click(login_cfg["submit_selector"])
            await page.wait_for_load_state("networkidle")
            print(f"\n[Diagnose] After login URL: {page.url}")

        # Now on the logged-in home page — detect site type
        is_spa = await page.evaluate("""
            () => {
                return !!(window.__NEXT_DATA__ || window.__reactFiber ||
                          window.angular || window.Vue ||
                          document.querySelector('[data-reactroot]') ||
                          document.querySelector('app-root'));
            }
        """)
        print(f"\n[Diagnose] Is this a JavaScript SPA (React/Angular/Vue)? {is_spa}")
        print(f"  → Set 'is_spa': {is_spa} in FLOOR_SYSTEM_CONFIG")

        print("\n[Diagnose] Browser is open. Navigate to a bill testimony page manually.")
        print("[Diagnose] Then come back here — we'll dump the page structure.")
        input("[Diagnose] Press ENTER once you are on a testimony page... ")

        # Dump all PDF-like links
        links = await page.query_selector_all("a[href]")
        pdf_links = []
        other_links = []
        for link in links:
            href = await link.get_attribute("href") or ""
            text = (await link.inner_text()).strip()
            if any(x in href.lower() for x in [".pdf", "download", "document", "testimony"]):
                pdf_links.append((text, href))
            else:
                other_links.append((text, href))

        print(f"\n[Diagnose] Likely document links ({len(pdf_links)} found):")
        for text, href in pdf_links[:20]:
            print(f"  '{text[:50]}' → {href}")

        print(f"\n[Diagnose] All other links ({len(other_links)} found, first 20):")
        for text, href in other_links[:20]:
            print(f"  '{text[:40]}' → {href}")

        await browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND LINE ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legislative document scraper")
    parser.add_argument("--mode", choices=["practice", "production", "diagnose"],
                        default="practice",
                        help="practice=congress.gov, production=Floor System, diagnose=inspect site")
    parser.add_argument("--bill-id", default="3076",
                        help="Bill ID (e.g. 3076 for congress.gov, HB1234 for Floor System)")
    parser.add_argument("--output-dir", default="storage/downloads")
    args = parser.parse_args()

    username = os.getenv("FLOOR_SYSTEM_USERNAME", "")
    password = os.getenv("FLOOR_SYSTEM_PASSWORD", "")

    if args.mode == "practice":
        print("=" * 60)
        print("PRACTICE MODE — using congress.gov")
        print("No login needed. Tests the full download flow.")
        print("=" * 60)
        result = asyncio.run(
            scrape_testimonies(
                config=CONGRESS_GOV_CONFIG,
                bill_id=args.bill_id,
                output_dir=args.output_dir,
            )
        )

    elif args.mode == "production":
        if not username or not password:
            print("ERROR: Set FLOOR_SYSTEM_USERNAME and FLOOR_SYSTEM_PASSWORD env vars")
            print("  export FLOOR_SYSTEM_USERNAME=youruser")
            print("  export FLOOR_SYSTEM_PASSWORD=yourpass")
            sys.exit(1)
        print("=" * 60)
        print("PRODUCTION MODE — using MGA Senate Floor System")
        print("=" * 60)
        result = asyncio.run(
            scrape_testimonies(
                config=FLOOR_SYSTEM_CONFIG,
                bill_id=args.bill_id,
                username=username,
                password=password,
                output_dir=args.output_dir,
            )
        )

    elif args.mode == "diagnose":
        print("=" * 60)
        print("DIAGNOSE MODE — inspect the Floor System structure")
        print("Run this FIRST on Monday before touching production config")
        print("=" * 60)
        asyncio.run(diagnose_site(FLOOR_SYSTEM_CONFIG, username, password))
        sys.exit(0)

    print("\nResult:")
    for f in result.get("downloaded_files", []):
        print(f"  Downloaded: {f}")
    for e in result.get("errors", []):
        print(f"  Error: {e}")