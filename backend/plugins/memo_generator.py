"""
Memo Generator Plugin
======================
Loads downloaded testimony PDFs + testimony table metadata + transcript,
then calls the AI API to generate a structured legislative analysis memo.

Supports four memo styles selectable by the user:
  full_analysis    — full 6-section briefing (default)
  quick_summary    — short executive overview (2–3 sections)
  amendments_focus — deep-dive on every amendment request
  opposition_report — who opposes and exactly why
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv
# load_dotenv()
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from datetime import datetime
from .base import BasePlugin




# ─────────────────────────────────────────────────────────────────────────────
# Prompt templates — same data, different analytical lens
# ─────────────────────────────────────────────────────────────────────────────

_SHARED_FOOTER = """
---

TESTIFIER ROSTER ({total_testifiers} total):
{testifier_roster}

---

WRITTEN TESTIMONY CONTENT:
{testimonies}

---

HEARING TRANSCRIPT:
{transcript}
"""


PROMPT_FULL_ANALYSIS = (
    """You are a legislative assistant advising a Member of Senate on issues of this Bill {bill_id}. 
        Use chain of thought to reason, write a memo explaining what are the position of each testimony, 
        who suggested this testimony, highlighting what amendments they are looking for if they are looking 
        for any, highlight any issues that may require future legislation, any questions, any themes emerging
        that demonstrate different approaches by party and any recommendations for future action We want to 
        specifically find out what the impact of the legislation could be for that organization. Show this 
        result clearly and prioritize accuracy and reference from the specific part of the document. 
        Output in a well format formal word document for me to download.
    """
    + _SHARED_FOOTER
)

# PROMPT_FULL_ANALYSIS = (
#     """You are a legislative assistant advising a Member of the Maryland Senate.

# Below is a list of all testifiers for {bill_id}, their organization, their position on the bill,
# and the full text of their written testimony (if submitted). Some testifiers gave oral testimony only.

# Write a professional legislative analysis memo covering ALL of the following sections:

# 1. SUMMARY OF TESTIMONY POSITIONS
#    For each testifier, state their name, organization, position (In Favor / Opposed /
#    In Favor with Amendments / Informational), and a 1–2 sentence summary of their key argument.

# 2. AMENDMENTS REQUESTED
#    List every specific amendment mentioned across all testimonies. Note who is requesting each one.

# 3. ISSUES REQUIRING FUTURE LEGISLATION
#    Identify problems raised that this bill does not address and may require separate legislation.

# 4. QUESTIONS RAISED
#    List open questions or requests for clarification raised by testifiers or implied by conflicts.

# 5. PARTY AND STAKEHOLDER THEMES
#    Which types of organisations support vs. oppose? Are there partisan patterns?
#    What are the major fault lines?

# 6. RECOMMENDATIONS FOR FUTURE ACTION
#    Based on the testimony, what should the Senator prioritise, watch out for, or follow up on?
# """
#     + _SHARED_FOOTER
# )

PROMPT_QUICK_SUMMARY = (
    """You are a legislative assistant advising a Member of the Maryland Senate.

Write a concise executive brief for {bill_id}. Senators are busy — keep it tight.

1. OVERALL PICTURE  (one paragraph)
   How many testified, rough breakdown (X for / Y against / Z with amendments),
   key stakeholder groups present, and the core controversy in plain language.

2. KEY VOICES  (top 5 most significant testifiers only)
   Bullet per person: name, organisation, position, one-sentence argument.

3. WATCH POINTS  (2–4 bullets)
   The most important issues the Senator must know before the committee vote.
"""
    + _SHARED_FOOTER
)

PROMPT_AMENDMENTS_FOCUS = (
    """You are a legislative assistant advising a Member of the Maryland Senate.

Analyse the testimony for {bill_id} and produce a focused AMENDMENTS ANALYSIS memo.

1. AMENDMENTS SUMMARY TABLE
   For each distinct amendment requested, list:
     - The specific change being requested
     - Who is requesting it (name / organisation)
     - Their position on the underlying bill
     - Whether other testifiers support or oppose this same change

2. COMMON GROUND AMENDMENTS
   Amendments requested by multiple parties across different positions —
   these are likely to gain traction in committee.

3. CONTESTED AMENDMENTS
   Amendments that some testifiers want and others explicitly oppose.
   Explain the competing interests.

4. DRAFTING IMPLICATIONS
   What would need to change in the bill text to satisfy the most common requests?
   Note any conflicts that could not be resolved simultaneously.
"""
    + _SHARED_FOOTER
)

PROMPT_OPPOSITION_REPORT = (
    """You are a legislative assistant advising a Member of the Maryland Senate.

Produce an OPPOSITION ANALYSIS memo for {bill_id} — focus entirely on those
who testified against or expressed serious reservations.

1. OPPOSITION ROSTER
   List every testifier who is Opposed or Favor with Amendments.
   For each: name, organisation, their exact objection in 1–2 sentences.

2. CORE OBJECTIONS  (ranked by how many testifiers raised each)
   Group the objections by theme. Which concerns are most widespread?

3. STRONGEST ARGUMENTS AGAINST
   The 2–3 arguments that are most substantive, well-evidenced, or politically
   significant — things the Senator cannot easily dismiss.

4. POTENTIAL CONCESSIONS
   What changes to the bill could neutralise the opposition or convert
   Favor with Amendments testifiers to full supporters?

5. IRREDUCIBLE OPPOSITION
   Which groups will oppose the bill regardless of amendments, and why?
"""
    + _SHARED_FOOTER
)

PROMPT_TEMPLATES = {
    "full_analysis":    PROMPT_FULL_ANALYSIS,
    "quick_summary":    PROMPT_QUICK_SUMMARY,
    "amendments_focus": PROMPT_AMENDMENTS_FOCUS,
    "opposition_report": PROMPT_OPPOSITION_REPORT,
}

TESTIFIER_ROW_TEMPLATE = (
    "  [{num}] {name} | {org} | {position_label}"
    "{written_note}"
)


# ─────────────────────────────────────────────────────────────────────────────
# Plugin
# ─────────────────────────────────────────────────────────────────────────────

class MemoPlugin(BasePlugin):
    name = "memo"
    description = "Generates a structured legislative analysis memo using AI (Claude / OpenAI)"

    def input_schema(self):
        return {
            "pdf_paths":         "list of strings — paths to downloaded testimony PDFs",
            "testimony_records": "list of dicts — rich metadata from scraper (name, org, position, etc.)",
            "transcript":        "string — hearing transcript text (optional)",
            "bill_id":           "string — bill identifier",
            "session":           "string — legislative session, e.g. '2026RS' (optional, for metadata)",
            "memo_style":        "string — one of: full_analysis | quick_summary | amendments_focus | opposition_report",
        }

    async def run(self, inputs: dict) -> dict:
        print(f"[MemoPlugin] OPENAI_API_KEY visible: {bool(os.getenv('OPENAI_API_KEY'))}")
        print(f"[MemoPlugin] ANTHROPIC_API_KEY visible: {bool(os.getenv('ANTHROPIC_API_KEY'))}")

        pdf_paths         = inputs.get("pdf_paths", [])
        testimony_records = inputs.get("testimony_records", [])
        transcript        = inputs.get("transcript", "").strip()
        bill_id           = inputs.get("bill_id", "unknown").strip()
        session           = inputs.get("session", "")
        memo_style        = inputs.get("memo_style", "full_analysis").strip()

        # Validate memo style — fall back to full analysis if unknown
        if memo_style not in PROMPT_TEMPLATES:
            memo_style = "full_analysis"

        # Prefer Claude; fall back to OpenAI
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        openai_key    = os.getenv("OPENAI_API_KEY", "")

        if not anthropic_key and not openai_key:
            return {
                "error": "No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your .env file.",
                "memo": "",
            }

        use_claude = bool(anthropic_key)

        # ── Step 1: Build testifier roster string ─────────────────────────
        roster_lines = []
        for i, rec in enumerate(testimony_records, 1):
            written_note = " [Written testimony attached]" if rec.get("pdf_filename") else " [Oral testimony only]"
            line = TESTIFIER_ROW_TEMPLATE.format(
                num            = i,
                name           = rec.get("name", "Unknown"),
                org            = rec.get("organization", "Unknown"),
                position_label = rec.get("position_label", rec.get("position", "")),
                written_note   = written_note,
            )
            roster_lines.append(line)

        testifier_roster = "\n".join(roster_lines) if roster_lines else "(No testifier data available)"

        # ── Step 2: Extract text from each PDF ───────────────────────────
        testimonies_text = ""
        pdf_errors       = []

        for pdf_path in pdf_paths:
            path = Path(pdf_path)
            if not path.exists():
                pdf_errors.append(f"File not found: {pdf_path}")
                continue
            try:
                import fitz  # PyMuPDF
                doc  = fitz.open(str(path))
                text = "\n".join(page.get_text() for page in doc)
                doc.close()

                filename = path.name
                matching = next(
                    (r for r in testimony_records if r.get("pdf_filename") == filename),
                    None,
                )
                if matching:
                    label = (
                        f"{matching['name']} | {matching.get('organization', '—')} | "
                        f"{matching.get('position_label', matching.get('position', ''))}"
                    )
                else:
                    label = filename

                testimonies_text += f"\n\n{'='*60}\nTESTIMONY: {label}\n{'='*60}\n{text}"

            except ImportError:
                return {"error": "PyMuPDF not installed. Run: pip install pymupdf", "memo": ""}
            except Exception as e:
                pdf_errors.append(f"Could not read {pdf_path}: {str(e)}")

        # ── Step 3: Guard against empty content ──────────────────────────
        if not testimonies_text and not transcript and not testimony_records:
            return {
                "error": (
                    "No testimony content available — no PDFs, no transcript, "
                    "and no testifier records. Cannot generate memo."
                ),
                "memo": "",
                "pdf_errors": pdf_errors,
            }

        if not testimonies_text:
            testimonies_text = (
                "(No written testimony PDFs available. "
                "Use the testifier roster and transcript above.)"
            )
        if not transcript:
            transcript = "(No hearing transcript was provided.)"

        # ── Step 4: Build the prompt ──────────────────────────────────────
        template = PROMPT_TEMPLATES[memo_style]
        prompt   = template.format(
            bill_id          = bill_id,
            total_testifiers = len(testimony_records),
            testifier_roster = testifier_roster,
            testimonies      = testimonies_text,
            transcript       = transcript,
        )

        # ── Step 5: Call AI API ───────────────────────────────────────────
        memo_text = ""

        if use_claude:
            try:
                import anthropic
                client  = anthropic.Anthropic(api_key=anthropic_key)
                message = client.messages.create(
                    model      = "claude-sonnet-4-6",
                    max_tokens = 4096,
                    messages   = [{"role": "user", "content": prompt}],
                )
                memo_text = message.content[0].text
                print(f"[MemoPlugin] Claude returned {len(memo_text)} chars ({memo_style})")
            except ImportError:
                return {"error": "Anthropic SDK not installed. Run: pip install anthropic", "memo": ""}
            except Exception as e:
                if openai_key:
                    print(f"[MemoPlugin] Claude failed ({e}), falling back to OpenAI…")
                    use_claude = False
                else:
                    return {"error": f"Claude API call failed: {str(e)}", "memo": ""}

        if not use_claude:
            try:
                from openai import OpenAI
                client   = OpenAI(api_key=openai_key)
                response = client.chat.completions.create(
                    model      = "gpt-4o",
                    max_tokens = 4096,
                    messages   = [{"role": "user", "content": prompt}],
                )
                memo_text = response.choices[0].message.content
                print(f"[MemoPlugin] OpenAI returned {len(memo_text)} chars ({memo_style})")
            except ImportError:
                return {"error": "OpenAI SDK not installed. Run: pip install openai", "memo": ""}
            except Exception as e:
                return {"error": f"OpenAI API call failed: {str(e)}", "memo": ""}

        api_used = "claude" if anthropic_key and use_claude else "openai"

        # ── Step 6: Save memo to disk ─────────────────────────────────────
        memo_dir  = Path(f"storage/memos/{bill_id}")
        memo_dir.mkdir(parents=True, exist_ok=True)
        memo_path = memo_dir / "memo.md"
        memo_path.write_text(memo_text, encoding="utf-8")
        print(f"[MemoPlugin] Saved memo → {memo_path}")

        # ── Step 7: Save metadata alongside memo ─────────────────────────
        # main.py will update drive_url in this file after uploading to Drive.
        metadata = {
            "bill_id":       bill_id,
            "session":       session,
            "generated_at":  datetime.now().isoformat(),
            "testifiers":    len(testimony_records),
            "pdf_count":     sum(1 for p in pdf_paths if Path(p).exists()),
            "api_used":      api_used,
            "memo_style":    memo_style,
            "drive_url":     "",
        }
        meta_path = memo_dir / "metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        return {
            "memo":                   memo_text,
            "memo_path":              str(memo_path),
            "pdf_errors":             pdf_errors,
            "testimonies_char_count": len(testimonies_text),
            "testifiers_included":    len(testimony_records),
            "api_used":               api_used,
            "memo_style":             memo_style,
        }
