"""
Floor System Scraper Plugin
============================
Connects to the MGA Senate Floor System (requires login + MGA wifi).

HOW THE FLOOR SYSTEM WORKS (discovered by the team):
  - It's a Single Page Application (SPA) — the URL never changes from
    https://senatefloor/floor regardless of what you click.
  - All data is loaded via XHR/fetch JSON API calls.
  - Login URL: https://senatefloor/floor/Accounts/Login?ReturnUrl=%2ffloor
  - Known API endpoints (captured from Network tab):
      GET /floor/bills/committeeannotaions?session=2026RS&_=<timestamp>
        → {"data":["HB1532","SB0165"],"messages":[],"success":true}
        (lists all bills that have committee testimony for the session)

DEBUGGING APPROACH for interns:
  - When the site is a SPA, open DevTools → Network tab → filter by "XHR" or "Fetch"
  - Click every button you care about and watch which requests fire
  - Copy the request URL and paste it below as a known endpoint
  - This scraper logs ALL intercepted API calls to the console so you can
    discover more endpoints as you use the site.

POSITION CODES:
  FWA = In Favor with Amendments
  FAV = In Favor
  UNF = Unfavorable / Opposed
  IMR = Informational / Neutral
"""

import asyncio
import os
import time
import json
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from .base import BasePlugin

POSITION_LABELS = {
    "FAV": "In Favor",
    "FWA": "In Favor with Amendments",
    "UNF": "Unfavorable / Opposed",
    "IMR": "Informational / Neutral",
    "":    "No position stated",
}

BASE_URL  = "https://senatefloor/floor"
LOGIN_URL = "https://senatefloor/floor/Accounts/Login"

# ─────────────────────────────────────────────────────────────────────────────
# Known API endpoint patterns (add more here as you discover them)
# ─────────────────────────────────────────────────────────────────────────────
def ts():
    """Cache-busting timestamp used by the Floor System as the _ param."""
    return int(time.time() * 1000)

def committee_annotations_url(session: str) -> str:
    return f"{BASE_URL}/bills/committeeannotaions?session={session}&_={ts()}"

# Guessed patterns for the testimony detail endpoint — add the real one here
# once you capture it from the Network tab.
TESTIMONY_ENDPOINT_PATTERNS = [
    "{base}/bills/committeetestimony?billId={bill_id}&session={session}&_={ts}",
    "{base}/bills/testimony?billId={bill_id}&session={session}&_={ts}",
    "{base}/bills/gettestimony?billId={bill_id}&session={session}&_={ts}",
    "{base}/testimony/{bill_id}?session={session}&_={ts}",
    "{base}/bills/{bill_id}/testimony?session={session}&_={ts}",
    "{base}/bills/details?billNumber={bill_id}&session={session}",
    "{base}/bills/committeeannotaions?session={session}&_={ts}",
    "{base}/bills/noted?session={session}&_={ts}",
]


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 helper: Login via requests.Session
# This gives us the auth cookies. We then share them with Playwright.
# ─────────────────────────────────────────────────────────────────────────────
def login_with_requests(username: str, password: str) -> requests.Session | None:
    """
    POST credentials to the Floor System login form.
    Returns an authenticated requests.Session on success, None on failure.

    Why requests instead of Playwright for login?
    - Faster and more reliable for simple form POSTs
    - No browser overhead
    - Easier to check the response code
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
    })

    # First GET the login page to pick up the CSRF token (__RequestVerificationToken)
    try:
        get_resp = session.get(LOGIN_URL, timeout=15, verify=False)
    except requests.RequestException as e:
        print(f"[FloorScraper] Cannot reach login page: {e}")
        return None

    # Extract the anti-forgery token from the login form HTML
    csrf_match = re.search(
        r'<input[^>]+name="__RequestVerificationToken"[^>]+value="([^"]+)"',
        get_resp.text
    )
    csrf_token = csrf_match.group(1) if csrf_match else ""
    if not csrf_token:
        print("[FloorScraper] Warning: CSRF token not found — POST may fail")

    # POST the login form
    try:
        post_resp = session.post(
            LOGIN_URL,
            data={
                "Username": username,
                "Password": password,
                "__RequestVerificationToken": csrf_token,
            },
            timeout=15,
            allow_redirects=True,
            verify=False,
        )
    except requests.RequestException as e:
        print(f"[FloorScraper] Login POST failed: {e}")
        return None

    # Check we're no longer on the login page
    if "Login" in post_resp.url or "login" in post_resp.url.lower():
        print(f"[FloorScraper] Login failed — still on login page: {post_resp.url}")
        return None

    print(f"[FloorScraper] Login OK via requests.Session. Cookies: {list(session.cookies.keys())}")
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 helper: Try known JSON API endpoints to get testimony data
# ─────────────────────────────────────────────────────────────────────────────
def try_json_api(
    http_session: requests.Session,
    bill_id: str,
    session_year: str,
) -> list[dict]:
    """
    Try every known and guessed JSON endpoint pattern to get testimony records.
    Returns a list of testimony dicts (may be empty if all endpoints fail).

    HOW TO ADD A NEW ENDPOINT:
      1. Open DevTools → Network → XHR
      2. Click the "Committee Testimony" tab for a bill
      3. Find the XHR request that returns testimony data
      4. Copy the URL pattern and add it to TESTIMONY_ENDPOINT_PATTERNS above
    """
    for pattern in TESTIMONY_ENDPOINT_PATTERNS:
        url = pattern.format(
            base    = BASE_URL,
            bill_id = bill_id,
            session = session_year,
            ts      = ts(),
        )
        try:
            resp = http_session.get(url, timeout=10, verify=False)
            if resp.status_code != 200:
                continue

            data = resp.json()
            print(f"[FloorScraper] API response from {url}: {str(data)[:200]}")

            # The floor system wraps results in {"data": [...], "success": true}
            records_raw = []
            if isinstance(data, dict) and data.get("success"):
                inner = data.get("data", [])
                if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                    records_raw = inner
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                records_raw = data

            if records_raw:
                print(f"[FloorScraper] ✓ Found {len(records_raw)} records via API: {url}")
                return records_raw

        except Exception as e:
            print(f"[FloorScraper] Endpoint {url} failed: {e}")

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 helper: Parse raw API records into our standard testimony_record format
# ─────────────────────────────────────────────────────────────────────────────
def normalise_api_record(raw: dict) -> dict:
    """
    The Floor System API uses different field names than our internal format.
    This function maps whatever the API returns to our standard schema.

    We try multiple possible field names so this works even if the API changes.
    Add more aliases below if you discover new field names from the Network tab.
    """
    def get(*keys):
        for k in keys:
            if k in raw:
                return str(raw[k]).strip()
        return ""

    name     = get("name", "Name", "testifierName", "TestifierName", "fullName")
    org      = get("organization", "Organization", "org", "Org", "affiliation")
    position = get("position", "Position", "stance", "Stance", "vote", "Vote").upper()
    pdf_url  = get("pdfUrl", "pdf_url", "testimonyUrl", "documentUrl", "href", "link")

    # Normalise position to our 4-letter code
    if "FAVOR" in position and "AMEND" in position:
        position = "FWA"
    elif "FAVOR" in position or position == "FAV":
        position = "FAV"
    elif "UNFAV" in position or "OPPOS" in position or position == "UNF":
        position = "UNF"
    else:
        position = position[:3] if len(position) >= 3 else "IMR"

    if position not in POSITION_LABELS:
        position = "IMR"

    return {
        "name":           name or "Unknown",
        "organization":   org  or "—",
        "position":       position,
        "position_label": POSITION_LABELS[position],
        "testimony_type": "Written" if pdf_url else "Oral",
        "pdf_url":        pdf_url,
        "pdf_filename":   "",   # filled in after download
    }


# ─────────────────────────────────────────────────────────────────────────────
# Plugin
# ─────────────────────────────────────────────────────────────────────────────

class FloorScraperPlugin(BasePlugin):
    name = "scraper"
    description = "Downloads written testimony PDFs from the MGA Senate Floor System (requires login)"

    def input_schema(self):
        return {
            "bill_id": "string — e.g. 'HB1532' (as shown in the Floor System)",
            "session": "string — e.g. '2026RS'",
        }

    async def run(self, inputs: dict) -> dict:
        bill_id      = (inputs.get("bill_id") or inputs.get("bill_number") or "").strip()
        session_year = inputs.get("session", "2026RS").strip()

        if not bill_id:
            return {"error": "bill_id is required", "downloaded_files": [], "count": 0,
                    "testimony_records": [], "total_testifiers": 0}

        username = os.getenv("FLOOR_SYSTEM_USERNAME", "")
        password = os.getenv("FLOOR_SYSTEM_PASSWORD", "")

        if not username or not password:
            return {
                "error": (
                    "FLOOR_SYSTEM_USERNAME and FLOOR_SYSTEM_PASSWORD must be set in .env. "
                    "These are your Senate Floor System credentials."
                ),
                "downloaded_files": [], "count": 0,
                "testimony_records": [], "total_testifiers": 0,
            }

        output_dir = Path(f"storage/downloads/{bill_id}")
        output_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files  = []
        testimony_records = []
        errors            = []

        # ── STEP 1: Login with requests.Session ───────────────────────────────
        print(f"[FloorScraper] Logging in as '{username}'…")
        http_session = login_with_requests(username, password)

        if http_session is None:
            # Fall back to Playwright-based login
            print("[FloorScraper] requests login failed — falling back to Playwright")
            return await self._playwright_fallback(
                bill_id, session_year, username, password,
                output_dir, downloaded_files, testimony_records, errors
            )

        # ── STEP 2: Verify the bill has testimony ─────────────────────────────
        ann_url  = committee_annotations_url(session_year)
        ann_resp = http_session.get(ann_url, timeout=10, verify=False)
        ann_data = {}
        try:
            ann_data = ann_resp.json()
            bills_with_testimony = ann_data.get("data", [])
            print(f"[FloorScraper] Bills with testimony this session: {bills_with_testimony}")
            if bill_id not in bills_with_testimony:
                print(f"[FloorScraper] Warning: {bill_id} not in the committee-annotations list")
        except Exception:
            pass

        # ── STEP 3: Try JSON API endpoints for testimony records ──────────────
        raw_records = try_json_api(http_session, bill_id, session_year)

        if raw_records:
            testimony_records = [normalise_api_record(r) for r in raw_records]
        else:
            print("[FloorScraper] No JSON API found — falling back to Playwright DOM scrape")
            return await self._playwright_fallback(
                bill_id, session_year, username, password,
                output_dir, downloaded_files, testimony_records, errors,
                http_session=http_session,
            )

        # ── STEP 4: Download PDFs for records that have a URL ─────────────────
        for i, record in enumerate(testimony_records):
            pdf_url = record.get("pdf_url", "")
            if not pdf_url:
                continue

            if pdf_url.startswith("/"):
                pdf_url = "https://senatefloor" + pdf_url
            elif not pdf_url.startswith("http"):
                pdf_url = BASE_URL + "/" + pdf_url.lstrip("/")

            try:
                resp = http_session.get(pdf_url, timeout=30, verify=False)
                if resp.status_code == 200:
                    safe  = re.sub(r"[^\w._-]", "_", f"{record['name']}_{record['organization']}")[:50]
                    fname = f"{i+1:03d}_{safe}.pdf"
                    fpath = output_dir / fname
                    fpath.write_bytes(resp.content)
                    downloaded_files.append(str(fpath))
                    record["pdf_filename"] = fname
                    print(f"[FloorScraper]   Downloaded: {fname}")
                else:
                    errors.append(f"HTTP {resp.status_code} for {record['name']}'s testimony")
            except Exception as e:
                errors.append(f"Failed to download {record['name']}: {str(e)}")

        print(f"[FloorScraper] Done: {len(downloaded_files)} PDFs, {len(testimony_records)} testifiers, {len(errors)} errors")

        return {
            "downloaded_files":  downloaded_files,
            "testimony_records": testimony_records,
            "documents_found":   [
                {"name": r.get("pdf_filename") or r["name"], "url": r.get("pdf_url", "")}
                for r in testimony_records
            ],
            "errors":           errors,
            "count":            len(downloaded_files),
            "bill_id":          bill_id,
            "total_testifiers": len(testimony_records),
            "session":          session_year,
        }

    # ── Playwright fallback: used when API discovery fails ────────────────────
    async def _playwright_fallback(
        self,
        bill_id:      str,
        session_year: str,
        username:     str,
        password:     str,
        output_dir:   Path,
        downloaded_files:  list,
        testimony_records: list,
        errors:       list,
        http_session: requests.Session | None = None,
    ) -> dict:
        """
        Full Playwright-based scraping with XHR interception.
        This is the fallback when the JSON API endpoints are unknown.

        KEY FEATURE: intercept_log captures every JSON API call the SPA makes.
        After running, check the uvicorn console — you'll see all the XHR URLs.
        Add the testimony one to TESTIMONY_ENDPOINT_PATTERNS above.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
                "downloaded_files": [], "testimony_records": [], "count": 0, "total_testifiers": 0,
            }

        intercept_log = {}   # url → parsed JSON body — inspect this to find the API

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
                accept_downloads=True,
            )

            # ── Inject cookies from requests.Session if available ─────────────
            if http_session:
                pw_cookies = [
                    {
                        "name":   c.name,
                        "value":  c.value,
                        "domain": c.domain or "senatefloor",
                        "path":   c.path or "/",
                    }
                    for c in http_session.cookies
                ]
                if pw_cookies:
                    await context.add_cookies(pw_cookies)
                    print(f"[FloorScraper] Injected {len(pw_cookies)} cookies into Playwright")

            page = await context.new_page()

            # ── XHR interception — capture all API calls ──────────────────────
            async def capture_response(response):
                url = response.url
                ct  = response.headers.get("content-type", "")
                if "senatefloor" in url and "json" in ct and response.status == 200:
                    try:
                        body = await response.json()
                        intercept_log[url] = body
                        print(f"[FloorScraper][XHR] {url}")
                        print(f"[FloorScraper][XHR] Response: {str(body)[:300]}")
                    except Exception:
                        pass

            page.on("response", capture_response)

            # ── Login if no cookies ───────────────────────────────────────────
            if not http_session:
                print("[FloorScraper] Playwright: navigating to login page…")
                await page.goto(
                    f"{LOGIN_URL}?ReturnUrl=%2ffloor",
                    wait_until="networkidle", timeout=20000
                )
                await page.fill("#Username", username)
                await page.fill("#Password", password)
                await asyncio.sleep(0.5)
                await page.click("button[type='submit']")
                await page.wait_for_load_state("networkidle", timeout=20000)

                if "Login" in page.url or "login" in page.url.lower():
                    await browser.close()
                    return {
                        "error": "Login failed — check FLOOR_SYSTEM_USERNAME / FLOOR_SYSTEM_PASSWORD",
                        "downloaded_files": [], "testimony_records": [], "count": 0, "total_testifiers": 0,
                    }
                print(f"[FloorScraper] Playwright login OK. URL: {page.url}")

            # ── Navigate to the floor system home ─────────────────────────────
            await page.goto(BASE_URL, wait_until="networkidle", timeout=20000)
            await asyncio.sleep(1)

            # ── Try to find and interact with the bill ────────────────────────
            # The SPA likely has a bill search box or dropdown
            bill_found = False
            try:
                # Common search box selectors in legislative SPA systems
                selectors = [
                    f"input[placeholder*='Bill']",
                    f"input[placeholder*='bill']",
                    f"input[placeholder*='Search']",
                    "#billSearch", "#bill-search", "#billId",
                    "input[name='billId']", "input[name='bill']",
                ]
                for sel in selectors:
                    if await page.query_selector(sel):
                        await page.fill(sel, bill_id)
                        await page.keyboard.press("Enter")
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        await asyncio.sleep(1)
                        bill_found = True
                        print(f"[FloorScraper] Found bill search box: {sel}")
                        break
            except Exception as e:
                print(f"[FloorScraper] Bill search interaction failed: {e}")

            # ── Try to click "Committee Testimony" tab ────────────────────────
            testimony_tab_selectors = [
                "a:has-text('Committee Testimony')",
                "button:has-text('Committee Testimony')",
                "a:has-text('Testimony')",
                "[data-tab='testimony']",
                "#committeeTestimony",
            ]
            for sel in testimony_tab_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click()
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        await asyncio.sleep(1.5)  # Give SPA time to fire XHR
                        print(f"[FloorScraper] Clicked testimony tab: {sel}")
                        break
                except Exception:
                    continue

            # ── Log all intercepted API calls for discovery ───────────────────
            print(f"\n[FloorScraper] === ALL INTERCEPTED API CALLS ({len(intercept_log)}) ===")
            for url, body in intercept_log.items():
                print(f"  URL: {url}")
                print(f"  Body: {str(body)[:400]}")
            print("[FloorScraper] === END INTERCEPTED CALLS ===\n")

            # ── Check if any intercepted call has testimony data ──────────────
            for url, body in intercept_log.items():
                if not isinstance(body, dict):
                    continue
                inner = body.get("data", [])
                if not isinstance(inner, list) or not inner:
                    continue
                if isinstance(inner[0], dict) and any(
                    k in inner[0] for k in ("name","Name","testifierName","fullName")
                ):
                    print(f"[FloorScraper] ✓ Found testimony data in intercepted call: {url}")
                    print(f"  → Add this URL pattern to TESTIMONY_ENDPOINT_PATTERNS in floor_scraper.py")
                    testimony_records = [normalise_api_record(r) for r in inner]
                    break

            # ── Fallback: DOM table scraping ──────────────────────────────────
            if not testimony_records:
                print("[FloorScraper] Trying DOM table scraping…")
                rows = await page.query_selector_all("table tbody tr")
                print(f"[FloorScraper] Found {len(rows)} table rows")
                for i, row in enumerate(rows):
                    cells = await row.query_selector_all("td")
                    if len(cells) < 3:
                        continue
                    name     = (await cells[0].inner_text()).strip() if len(cells) > 0 else ""
                    org      = (await cells[1].inner_text()).strip() if len(cells) > 1 else ""
                    position = (await cells[2].inner_text()).strip().upper() if len(cells) > 2 else ""
                    if position not in POSITION_LABELS:
                        position = "IMR"
                    pdf_el  = await row.query_selector("td:nth-child(4) a[href]")
                    pdf_href = (await pdf_el.get_attribute("href") or "").strip() if pdf_el else ""
                    testimony_records.append({
                        "name":           name or f"Testifier {i+1}",
                        "organization":   org or "—",
                        "position":       position,
                        "position_label": POSITION_LABELS.get(position, "Informational"),
                        "testimony_type": "Written" if pdf_href else "Oral",
                        "pdf_url":        pdf_href,
                        "pdf_filename":   "",
                    })

            # ── Download PDFs ─────────────────────────────────────────────────
            for i, record in enumerate(testimony_records):
                pdf_url = record.get("pdf_url", "")
                if not pdf_url:
                    continue
                if pdf_url.startswith("/"):
                    pdf_url = "https://senatefloor" + pdf_url
                try:
                    resp = await page.request.get(pdf_url, timeout=30000)
                    if resp.ok:
                        safe  = re.sub(r"[^\w._-]", "_", f"{record['name']}_{record['organization']}")[:50]
                        fname = f"{i+1:03d}_{safe}.pdf"
                        fpath = output_dir / fname
                        fpath.write_bytes(await resp.body())
                        downloaded_files.append(str(fpath))
                        record["pdf_filename"] = fname
                        print(f"[FloorScraper]   Downloaded: {fname}")
                    else:
                        errors.append(f"HTTP {resp.status} for {record['name']}")
                except Exception as e:
                    errors.append(f"PDF download failed for {record['name']}: {e}")

            await browser.close()

        print(f"[FloorScraper] Playwright fallback done: {len(downloaded_files)} PDFs, {len(errors)} errors")

        return {
            "downloaded_files":  downloaded_files,
            "testimony_records": testimony_records,
            "documents_found":   [
                {"name": r.get("pdf_filename") or r["name"], "url": r.get("pdf_url", "")}
                for r in testimony_records
            ],
            "errors":           errors,
            "count":            len(downloaded_files),
            "bill_id":          bill_id,
            "total_testifiers": len(testimony_records),
            "session":          session_year,
            "intercepted_api_calls": list(intercept_log.keys()),
        }
