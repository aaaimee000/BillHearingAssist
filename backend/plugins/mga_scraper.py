"""
MGA (Maryland General Assembly) Scraper Plugin
================================================
Practice target: mgaleg.maryland.gov (public, no login needed)
Production target: Senate Floor System (login required, swap on Monday)

KEY INSIGHT from inspecting mgaleg.maryland.gov:
  - Bill text PDFs are publicly accessible with a predictable URL pattern
  - No Playwright needed for the PDF download — plain HTTP works
  - The video is a custom player (not YouTube), so we handle it differently:
      * We store the video URL and show it as a clickable link for staff
      * Auto-transcription via stream extraction is Option A (future work)
      * Manual transcript paste is Option B (what we build now)

MGA URL PATTERNS (discovered by inspection):
  Bill text PDF:   https://mgaleg.maryland.gov/{session}/bills/{chamber}/{bill_number}T.pdf
  Written testimony: https://mgaleg.maryland.gov/{session}/testimony/{bill_number}/
  Video hearing:   https://mgaleg.maryland.gov/mgawebsite/Committees/Media/false?...

  Examples:
    HB1532 text:    https://mgaleg.maryland.gov/2026RS/bills/hb/hb1532T.pdf
    SB0100 text:    https://mgaleg.maryland.gov/2026RS/bills/sb/sb0100T.pdf
"""

import re
import os
import requests
from pathlib import Path
from .base import BasePlugin


# ─────────────────────────────────────────────────────────────────────────────
# MGA-specific URL builders
# ─────────────────────────────────────────────────────────────────────────────

def build_mga_pdf_url(bill_number: str, session: str) -> str:
    """
    Builds the direct PDF URL for a Maryland bill.

    Input:  bill_number="hb1532"  session="2026RS"
    Output: "https://mgaleg.maryland.gov/2026RS/bills/hb/hb1532T.pdf"

    The pattern is:
      - Lowercase the bill number
      - Chamber prefix is the first two letters (hb = house bill, sb = senate bill)
      - Append 'T' before .pdf for the text version
    """
    bill_lower = bill_number.lower().strip()

    # Extract chamber prefix: "hb1532" → "hb", "sb0100" → "sb"
    match = re.match(r'^([a-z]+)(\d+)$', bill_lower)
    if not match:
        return ""

    chamber_prefix = match.group(1)   # "hb" or "sb"
    number_part    = match.group(2)   # "1532"
    full_number    = f"{chamber_prefix}{number_part.zfill(4)}"  # zero-pad to 4 digits

    return f"https://mgaleg.maryland.gov/{session}/bills/{chamber_prefix}/{full_number}T.pdf"


def build_mga_testimony_url(bill_number: str, session: str) -> str:
    """
    Builds the testimony listing page URL for a Maryland bill.
    We'll scrape this page to find individual testimony PDF links.
    """
    bill_lower = bill_number.lower().strip()
    match = re.match(r'^([a-z]+)(\d+)$', bill_lower)
    if not match:
        return ""
    chamber_prefix = match.group(1)
    number_part    = match.group(2)
    full_number    = f"{chamber_prefix}{number_part.zfill(4)}"
    return f"https://mgaleg.maryland.gov/{session}/testimony/{full_number}/"


# ─────────────────────────────────────────────────────────────────────────────
# Floor System config (for Monday — production use)
# ─────────────────────────────────────────────────────────────────────────────

FLOOR_SYSTEM_CONFIG = {
    "name": "MGA Senate Floor System (production)",
    "requires_login": True,
    "login": {
        "url": "https://senatefloor/floor/login",
        "username_selector": "#username",
        "password_selector": "#password",
        "submit_selector": "button[type='submit']",
        "post_login_wait": "networkidle",
    },
    "bill_url_template": "https://senatefloor/floor/bills/{bill_id}/testimony",
    "selectors": {
        "pdf_links":             "a[href$='.pdf']",
        "page_ready_indicator":  "main",
        "testimony_section":     None,
    },
    "timeouts": {
        "page_load": 20000,
        "element":   15000,
        "download":  60000,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Plugin
# ─────────────────────────────────────────────────────────────────────────────

class MGAScraperPlugin(BasePlugin):
    name = "scraper"
    description = "Downloads Maryland bill PDFs and testimony documents from mgaleg.maryland.gov"

    def input_schema(self):
        return {
            "bill_number": "string — e.g. 'hb1532' or 'sb0100'",
            "session":     "string — e.g. '2026RS' (default: 2026RS)",
            "video_url":   "string — MGA hearing video URL (stored for display, not auto-transcribed yet)",
        }

    async def run(self, inputs: dict) -> dict:
        bill_number = inputs.get("bill_number", "").strip().lower()
        session     = inputs.get("session", "2026RS").strip()
        video_url   = inputs.get("video_url", "").strip()

        # ── Input validation ──────────────────────────────────────────────
        if not bill_number:
            return {
                "error": "bill_number is required (e.g. 'hb1532')",
                "downloaded_files": [], "count": 0,
            }

        if not re.match(r'^[a-z]{2}\d+$', bill_number):
            return {
                "error": (
                    f"bill_number '{bill_number}' doesn't look right. "
                    "Expected format: 'hb1532' or 'sb0100' — "
                    "two letters (chamber) followed by digits (number)."
                ),
                "downloaded_files": [], "count": 0,
            }

        # ── Build URLs ────────────────────────────────────────────────────
        pdf_url       = build_mga_pdf_url(bill_number, session)
        testimony_url = build_mga_testimony_url(bill_number, session)

        if not pdf_url:
            return {
                "error": f"Could not build PDF URL for bill '{bill_number}'",
                "downloaded_files": [], "count": 0,
            }

        output_dir = Path(f"storage/downloads/mga_{session}_{bill_number}")
        output_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files = []
        documents_found  = []
        errors           = []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

        # ── Step 1: Download the bill text PDF ───────────────────────────
        print(f"[MGA Scraper] Downloading bill text: {pdf_url}")
        try:
            response = requests.get(pdf_url, headers=headers, timeout=30)

            if response.status_code == 200 and response.headers.get("content-type", "").startswith("application/pdf"):
                filename  = f"{bill_number}_text.pdf"
                file_path = output_dir / filename
                file_path.write_bytes(response.content)
                downloaded_files.append(str(file_path))
                documents_found.append({
                    "name": f"{bill_number.upper()} — Bill Text",
                    "url":  pdf_url,
                    "type": "bill_text",
                })
                print(f"[MGA Scraper] Downloaded bill text: {file_path}")
            else:
                errors.append(
                    f"Bill text PDF not found (HTTP {response.status_code}). "
                    f"Check bill number and session. URL tried: {pdf_url}"
                )
                print(f"[MGA Scraper] PDF not found: HTTP {response.status_code}")

        except requests.RequestException as e:
            errors.append(f"Network error downloading bill text: {str(e)}")

        # ── Step 2: Try to find written testimony PDFs ────────────────────
        # MGA's testimony pages are JavaScript-rendered, so we try a few
        # known URL patterns for testimony documents.
        print(f"[MGA Scraper] Looking for testimony documents...")

        # Pattern 1: testimony index page (may or may not exist)
        testimony_patterns = [
            testimony_url,
            f"https://mgaleg.maryland.gov/{session}/testimony/{bill_number.upper()}/",
        ]

        for t_url in testimony_patterns:
            try:
                t_response = requests.get(t_url, headers=headers, timeout=15)
                if t_response.status_code == 200:
                    # Look for PDF links in the HTML
                    pdf_links = re.findall(
                        r'href=["\']([^"\']*\.pdf)["\']',
                        t_response.text,
                        re.IGNORECASE
                    )
                    for link in pdf_links:
                        # Make relative URLs absolute
                        if link.startswith("/"):
                            link = "https://mgaleg.maryland.gov" + link
                        documents_found.append({
                            "name": link.split("/")[-1],
                            "url":  link,
                            "type": "testimony",
                        })
                    if pdf_links:
                        print(f"[MGA Scraper] Found {len(pdf_links)} testimony links at {t_url}")
                        break
            except requests.RequestException:
                pass

        # Download any testimony PDFs we found
        for i, doc in enumerate(documents_found):
            if doc["type"] != "testimony":
                continue
            try:
                r = requests.get(doc["url"], headers=headers, timeout=30)
                if r.status_code == 200:
                    safe_name = re.sub(r'[^\w._-]', '_', doc["name"])[:60]
                    file_path = output_dir / f"testimony_{i:02d}_{safe_name}"
                    if not str(file_path).endswith(".pdf"):
                        file_path = Path(str(file_path) + ".pdf")
                    file_path.write_bytes(r.content)
                    downloaded_files.append(str(file_path))
            except requests.RequestException as e:
                errors.append(f"Failed to download testimony {doc['url']}: {str(e)}")

        # ── Step 3: Build result ──────────────────────────────────────────
        result = {
            "downloaded_files": downloaded_files,
            "documents_found":  documents_found,
            "errors":           errors,
            "count":            len(downloaded_files),
            "bill_number":      bill_number.upper(),
            "session":          session,
            "pdf_url":          pdf_url,
            "testimony_url":    testimony_url,
            "video_url":        video_url,   # passed through for frontend to display
            "bill_label":       f"{bill_number.upper()} ({session})",
        }

        # If we found nothing at all, give a helpful error
        if not downloaded_files and not errors:
            result["error"] = (
                f"No documents found for {bill_number.upper()} in session {session}. "
                f"Double-check the bill number and session year."
            )

        return result
