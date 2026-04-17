"""
Floor System Scraper Plugin
============================
Connects to the MGA Senate Floor System (requires login + MGA wifi).

HOW THE FLOOR SYSTEM WORKS (discovered from Network tab analysis):
  - SPA at https://senatefloor/floor — URL never changes.
  - Login: POST to https://senatefloor/floor/Accounts/Login with CSRF token.
  - After login, GET /floor/bills/details?billNumber=HB1532&session=2026RS
    → Returns 29.3 kB of HTML containing the testimony table.
  - Testimony PDF links in that HTML point to:
      https://mgaleg.maryland.gov/ctudata/{year}/{committee}/{hash}.pdf
  - Those mgaleg.gov/ctudata URLs are PUBLICLY DOWNLOADABLE — no auth needed.
  - Clicking a testimony in the UI only fires a SignalR ping (no separate XHR).

SCRAPING STRATEGY (verified):
  1. Login with requests.Session + CSRF token
  2. GET /floor/bills/details?billNumber={bill}&session={session}
  3. Parse HTML for all mgaleg.maryland.gov/ctudata/ PDF links
  4. Also parse the <tr> rows surrounding those links for name/org/position
  5. Download PDFs directly from mgaleg.gov (public, no auth)

KNOWN API ENDPOINTS:
  GET /floor/bills/committeeannotaions?session=2026RS&_=<ts>
    → {"data":["HB1532","SB0165"],"messages":[],"success":true}
    (lists bills with committee testimony in the session)

  GET /floor/bills/details?billNumber=HB1532&session=2026RS
    → 29.3 kB HTML with testimony table + PDF links (KEY ENDPOINT)

POSITION CODES:
  FWA = In Favor with Amendments
  FAV = In Favor
  UNF = Unfavorable / Opposed
  IMR = Informational / Neutral
"""

import os
import re
import time
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

# Map of position strings (from HTML) → internal code
POSITION_MAP = {
    "FAV":                          "FAV",
    "FAVORABLE":                    "FAV",
    "IN FAVOR":                     "FAV",
    "FWA":                          "FWA",
    "FAVORABLE WITH AMENDMENTS":    "FWA",
    "IN FAVOR WITH AMENDMENTS":     "FWA",
    "UNF":                          "UNF",
    "UNFAVORABLE":                  "UNF",
    "OPPOSED":                      "UNF",
    "IMR":                          "IMR",
    "INFORMATIONAL":                "IMR",
    "INFO":                         "IMR",
}


def ts():
    """Cache-busting timestamp used by the Floor System as the _ param."""
    return int(time.time() * 1000)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Login via requests.Session
# ─────────────────────────────────────────────────────────────────────────────
def login_with_requests(username: str, password: str) -> requests.Session | None:
    """
    POST credentials to the Floor System login form.
    Returns an authenticated requests.Session on success, None on failure.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
    })

    # GET the login page to extract CSRF token
    try:
        get_resp = session.get(LOGIN_URL, timeout=15, verify=False)
    except requests.RequestException as e:
        print(f"[FloorScraper] Cannot reach login page: {e}")
        return None

    csrf_match = re.search(
        r'<input[^>]+name="__RequestVerificationToken"[^>]+value="([^"]+)"',
        get_resp.text
    )
    csrf_token = csrf_match.group(1) if csrf_match else ""
    if not csrf_token:
        print("[FloorScraper] Warning: CSRF token not found in login page")

    # POST credentials
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

    if "login" in post_resp.url.lower():
        print(f"[FloorScraper] Login failed — still on login page: {post_resp.url}")
        return None

    print(f"[FloorScraper] Login OK. Cookies: {list(session.cookies.keys())}")
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Scrape the bill details page HTML for testimony PDF links
# ─────────────────────────────────────────────────────────────────────────────
def scrape_details_html(
    http_session: requests.Session,
    bill_id: str,
    session_year: str,
) -> list[dict]:
    """
    GET the Floor System bill details page and parse HTML for testimony data.

    KEY DISCOVERY (from Network tab):
      URL: GET /floor/bills/details?billNumber=HB1532&session=2026RS
      Response: 29.3 kB HTML containing the testimony table with PDF links.
      PDF links point to: mgaleg.maryland.gov/ctudata/{year}/{committee}/{hash}.pdf
      Those PDF URLs are publicly accessible — no authentication required.
    """
    url = f"{BASE_URL}/bills/details?billNumber={bill_id}&session={session_year}"
    print(f"[FloorScraper] Fetching bill details page: {url}")

    try:
        resp = http_session.get(url, timeout=15, verify=False)
    except requests.RequestException as e:
        print(f"[FloorScraper] Details page fetch failed: {e}")
        return []

    if resp.status_code != 200:
        print(f"[FloorScraper] Details page returned HTTP {resp.status_code}")
        return []

    html = resp.text
    print(f"[FloorScraper] Details page: {len(html)} chars")

    # ── Find all mgaleg ctudata PDF URLs embedded in the HTML ────────────────
    ctudata_re = re.compile(
        r'https?://mgaleg\.maryland\.gov/ctudata/[^\s"\'<>&]+\.pdf',
        re.IGNORECASE
    )
    pdf_urls = list(dict.fromkeys(ctudata_re.findall(html)))  # deduplicate, preserve order
    print(f"[FloorScraper] Found {len(pdf_urls)} testimony PDF URL(s)")

    if not pdf_urls:
        # Print an HTML sample to help diagnose the structure
        print(f"[FloorScraper] No ctudata URLs found. HTML sample:\n{html[:3000]}")
        return []

    # ── Parse <tr> rows to get testifier name / org / position ───────────────
    # Expected table structure (approximate):
    #   <tr>
    #     <td>Name</td>
    #     <td>Organization</td>
    #     <td>FAV</td>
    #     <td><a href="https://mgaleg.maryland.gov/ctudata/...">Download</a></td>
    #   </tr>

    tr_re  = re.compile(r'<tr[^>]*>(.*?)</tr>',   re.DOTALL | re.IGNORECASE)
    td_re  = re.compile(r'<td[^>]*>(.*?)</td>',   re.DOTALL | re.IGNORECASE)
    tag_re = re.compile(r'<[^>]+>')

    records      = []
    matched_urls = set()

    for tr_m in tr_re.finditer(html):
        tr_html = tr_m.group(1)
        if "ctudata" not in tr_html.lower():
            continue

        row_pdfs = ctudata_re.findall(tr_html)
        if not row_pdfs:
            continue

        pdf_url = row_pdfs[0]
        matched_urls.add(pdf_url)

        # Extract and clean cell text
        cells = [
            re.sub(r'\s+', ' ', tag_re.sub('', td.group(1))).strip()
            for td in td_re.finditer(tr_html)
        ]
        cells = [c for c in cells if c and not c.startswith("http")]

        position = "IMR"
        name     = ""
        org      = ""

        for cell in cells:
            cu = cell.upper().strip()
            if cu in POSITION_MAP:
                position = POSITION_MAP[cu]
            elif not name and 3 <= len(cell) <= 80:
                name = cell
            elif not org and 3 <= len(cell) <= 120:
                org = cell

        records.append({
            "name":           name or f"Testifier {len(records) + 1}",
            "organization":   org  or "—",
            "position":       position,
            "position_label": POSITION_LABELS[position],
            "testimony_type": "Written",
            "pdf_url":        pdf_url,
            "pdf_filename":   "",
        })

    # Add any PDF URLs not found in a parsed table row
    for pdf_url in pdf_urls:
        if pdf_url not in matched_urls:
            records.append({
                "name":           f"Testifier {len(records) + 1}",
                "organization":   "—",
                "position":       "IMR",
                "position_label": POSITION_LABELS["IMR"],
                "testimony_type": "Written",
                "pdf_url":        pdf_url,
                "pdf_filename":   "",
            })

    print(f"[FloorScraper] Parsed {len(records)} testimony record(s) from details HTML")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Plugin
# ─────────────────────────────────────────────────────────────────────────────

class FloorScraperPlugin(BasePlugin):
    name = "scraper"
    description = "Downloads written testimony PDFs from the MGA Senate Floor System (requires Senate network login)"

    def input_schema(self):
        return {
            "bill_id": "string — e.g. 'HB1532' (as shown in the Floor System)",
            "session": "string — e.g. '2026RS'",
        }

    async def run(self, inputs: dict) -> dict:
        bill_id      = (inputs.get("bill_id") or inputs.get("bill_number") or "").strip().upper()
        session_year = inputs.get("session", "2026RS").strip()

        if not bill_id:
            return {
                "error": "bill_id is required (e.g. 'HB1532')",
                "downloaded_files": [], "count": 0,
                "testimony_records": [], "total_testifiers": 0,
            }

        username = os.getenv("FLOOR_SYSTEM_USERNAME", "")
        password = os.getenv("FLOOR_SYSTEM_PASSWORD", "")

        output_dir = Path(f"storage/downloads/{bill_id}")
        output_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files  = []
        testimony_records = []
        errors            = []

        # ── No credentials → cannot reach Floor System ────────────────────────
        if not username or not password:
            return {
                "error": (
                    "FLOOR_SYSTEM_USERNAME and FLOOR_SYSTEM_PASSWORD must be set in "
                    "backend/.env to auto-download from the Senate Floor System. "
                    "Please upload testimony PDFs manually using the Upload tab."
                ),
                "downloaded_files": [], "count": 0,
                "testimony_records": [], "total_testifiers": 0,
                "bill_id": bill_id,
                "session": session_year,
                "requires_manual_upload": True,
            }

        # ── Step 1: Login ─────────────────────────────────────────────────────
        print(f"[FloorScraper] Logging in as '{username}'…")
        http_session = login_with_requests(username, password)

        if http_session is None:
            return {
                "error": (
                    "Login to Senate Floor System failed. "
                    "Check FLOOR_SYSTEM_USERNAME / FLOOR_SYSTEM_PASSWORD in .env, "
                    "and ensure you are on the Senate network."
                ),
                "downloaded_files": [], "count": 0,
                "testimony_records": [], "total_testifiers": 0,
                "bill_id": bill_id,
                "session": session_year,
                "requires_manual_upload": True,
            }

        # ── Step 2: Download bill text from mgaleg (public) ───────────────────
        # Bill text PDF is at: mgaleg.maryland.gov/{session}/bills/{chamber}/{billT.pdf}
        bill_lower = bill_id.lower()
        match = re.match(r'^([a-z]+)(\d+)$', bill_lower)
        # if match:
        #     chamber = match.group(1)
        #     num     = match.group(2).zfill(4)
        #     bill_text_url = (
        #         f"https://mgaleg.maryland.gov/{session_year}/bills/{chamber}/{chamber}{num}T.pdf"
        #     )
        #     print(f"[FloorScraper] Downloading bill text: {bill_text_url}")
        #     try:
        #         r = http_session.get(bill_text_url, timeout=30, verify=False)
        #         if r.status_code == 200 and "pdf" in r.headers.get("content-type", "").lower():
        #             bill_text_path = output_dir / f"{bill_id}_bill_text.pdf"
        #             bill_text_path.write_bytes(r.content)
        #             # Bill text is NOT included in testimony_records (it's the bill, not testimony)
        #             print(f"[FloorScraper] Bill text saved: {bill_text_path}")
        #         else:
        #             errors.append(f"Bill text not found (HTTP {r.status_code})")
        #     except Exception as e:
        #         errors.append(f"Bill text download failed: {e}")

        # ── Step 3: Scrape details page for testimony PDF links ───────────────
        testimony_records_raw = scrape_details_html(http_session, bill_id, session_year)

        if not testimony_records_raw:
            msg = (
                f"No testimony PDFs found in the Floor System for {bill_id}. "
                "Either no testimony has been filed, or the details page structure "
                "has changed. You can upload testimony PDFs manually."
            )
            errors.append(msg)
            print(f"[FloorScraper] {msg}")
            return {
                "downloaded_files":    [],
                "testimony_records":   [],
                "documents_found":     [],
                "errors":              errors,
                "count":               0,
                "bill_id":             bill_id,
                "total_testifiers":    0,
                "session":             session_year,
                "requires_manual_upload": True,
            }

        testimony_records = testimony_records_raw

        # ── Step 4: Download PDFs from mgaleg.gov (public URLs, no auth needed) ─
        print(f"[FloorScraper] Downloading {len(testimony_records)} testimony PDF(s)…")
        for i, record in enumerate(testimony_records):
            pdf_url = record.get("pdf_url", "")
            if not pdf_url:
                continue

            try:
                resp = requests.get(pdf_url, timeout=30)  # plain requests, no auth needed
                if resp.status_code == 200:
                    # Use hash from URL as filename (guaranteed unique)
                    url_hash = pdf_url.rstrip("/").split("/")[-1]
                    fname    = f"{i + 1:03d}_{url_hash}"
                    fpath    = output_dir / fname
                    fpath.write_bytes(resp.content)
                    downloaded_files.append(str(fpath))
                    record["pdf_filename"] = fname
                    print(f"[FloorScraper]   Downloaded: {fname}")
                else:
                    errors.append(
                        f"HTTP {resp.status_code} for {record.get('name', 'testifier')} testimony"
                    )
            except Exception as e:
                errors.append(f"Failed to download {record.get('name', 'testifier')}: {e}")

        print(
            f"[FloorScraper] Done: {len(downloaded_files)} PDFs downloaded, "
            f"{len(testimony_records)} testifiers, {len(errors)} errors"
        )

        return {
            "downloaded_files":  downloaded_files,
            "testimony_records": testimony_records,
            "documents_found": [
                {"name": r.get("pdf_filename") or r["name"], "url": r.get("pdf_url", "")}
                for r in testimony_records
            ],
            "errors":           errors,
            "count":            len(downloaded_files),
            "bill_id":          bill_id,
            "total_testifiers": len(testimony_records),
            "session":          session_year,
        }
