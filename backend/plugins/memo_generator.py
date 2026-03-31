"""
Memo Generator Plugin
======================
Loads downloaded testimony PDFs + testimony table metadata + transcript,
then calls the AI API to generate a structured legislative analysis memo.

KEY IMPROVEMENT OVER PREVIOUS VERSION:
  The scraper now returns `testimony_records` — a list of dicts with each
  testifier's name, organization, position (FWA/UNF/FAV/IMR), and whether
  they submitted written testimony.

  We pass this structured context into the prompt so the AI can write:
    "Jorge Aguilar of Food & Water Watch testified UNFAVORABLY, arguing..."
  instead of just dumping raw PDF text and hoping the AI figures it out.

  This makes the memo dramatically more useful because:
    - Every testifier is named and attributed correctly
    - Position (for/against/neutral) is stated clearly
    - Oral-only testifiers are noted even without a PDF
"""

import os
from pathlib import Path
from .base import BasePlugin

from dotenv import load_dotenv
load_dotenv()

MEMO_PROMPT_TEMPLATE = """You are a legislative assistant advising a Member of the Maryland Senate.

Below is a list of all testifiers for {bill_id}, their organization, their position on the bill,
and the full text of their written testimony (if submitted). Some testifiers gave oral testimony only.

Your task is to write a professional legislative analysis memo that covers ALL of the following:

1. SUMMARY OF TESTIMONY POSITIONS
   For each testifier, state their name, organization, their position (In Favor / Opposed /
   In Favor with Amendments / Informational), and a 1-2 sentence summary of their key argument.

2. AMENDMENTS REQUESTED
   List any specific amendments mentioned across all testimonies. Note who is requesting each.

3. ISSUES REQUIRING FUTURE LEGISLATION
   Identify any problems raised that this bill does not address and may require separate legislation.

4. QUESTIONS RAISED
   List any open questions or requests for clarification raised by testifiers or implied by conflicting positions.

5. PARTY AND STAKEHOLDER THEMES
   Identify patterns — which types of organizations support vs. oppose? Are there partisan patterns?
   What are the major fault lines?

6. RECOMMENDATIONS FOR FUTURE ACTION
   Based on the testimony, what should the Senator prioritize, watch out for, or follow up on?

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

TESTIFIER_ROW_TEMPLATE = (
    "  [{num}] {name} | {org} | {position_label}"
    "{written_note}"
)


class MemoPlugin(BasePlugin):
    name = "memo"
    description = "Generates a structured legislative analysis memo using AI"

    def input_schema(self):
        return {
            "pdf_paths":         "list of strings — paths to downloaded testimony PDFs",
            "testimony_records": "list of dicts — rich metadata from scraper (name, org, position, etc.)",
            "transcript":        "string — hearing transcript text (optional)",
            "bill_id":           "string — bill identifier",
        }

    async def run(self, inputs: dict) -> dict:
        pdf_paths         = inputs.get("pdf_paths", [])
        testimony_records = inputs.get("testimony_records", [])
        transcript        = inputs.get("transcript", "").strip()
        bill_id           = inputs.get("bill_id", "unknown").strip()

        # Support both Claude and OpenAI keys — try Claude first
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        openai_key    = os.getenv("OPENAI_API_KEY", "")

        if not anthropic_key and not openai_key:
            return {
                "error": "No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your .env file.",
                "memo": ""
            }

        use_claude = bool(anthropic_key)

        # ── Step 1: Build the testifier roster string ─────────────────────
        # This goes into the prompt header so the AI knows every person
        # even if they only gave oral testimony (no PDF to extract).
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
        # Map filename → extracted text so we can pair with the right record
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

                # Find matching record by filename for better labelling
                filename = path.name
                matching = next(
                    (r for r in testimony_records if r.get("pdf_filename") == filename),
                    None
                )
                if matching:
                    label = (
                        f"{matching['name']} | {matching['organization']} | "
                        f"{matching['position_label']}"
                    )
                else:
                    label = filename

                testimonies_text += f"\n\n{'='*60}\nTESTIMONY: {label}\n{'='*60}\n{text}"

            except ImportError:
                return {"error": "PyMuPDF not installed. Run: pip install pymupdf", "memo": ""}
            except Exception as e:
                pdf_errors.append(f"Could not read {pdf_path}: {str(e)}")

        # ── Step 3: Handle case where nothing is available ────────────────
        if not testimonies_text and not transcript and not testimony_records:
            return {
                "error": (
                    "No testimony content available — no PDFs, no transcript, "
                    "and no testifier records. Cannot generate memo."
                ),
                "memo": "",
                "pdf_errors": pdf_errors,
            }

        # Fill in defaults for missing content
        if not testimonies_text:
            testimonies_text = (
                "(No written testimony PDFs were available. "
                "Use the testifier roster and transcript above.)"
            )
        if not transcript:
            transcript = "(No hearing transcript was provided.)"

        # ── Step 4: Build the prompt ──────────────────────────────────────
        prompt = MEMO_PROMPT_TEMPLATE.format(
            bill_id          = bill_id,
            total_testifiers = len(testimony_records),
            testifier_roster = testifier_roster,
            testimonies      = testimonies_text,
            transcript       = transcript,
        )

        # ── Step 5: Call the AI API ───────────────────────────────────────
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
                print(f"[MemoPlugin] Claude API returned {len(memo_text)} chars")
            except ImportError:
                return {"error": "Anthropic SDK not installed. Run: pip install anthropic", "memo": ""}
            except Exception as e:
                # Fall through to OpenAI if Claude fails and key exists
                if openai_key:
                    print(f"[MemoPlugin] Claude failed ({e}), trying OpenAI...")
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
                print(f"[MemoPlugin] OpenAI API returned {len(memo_text)} chars")
            except ImportError:
                return {"error": "OpenAI SDK not installed. Run: pip install openai", "memo": ""}
            except Exception as e:
                return {"error": f"OpenAI API call failed: {str(e)}", "memo": ""}

        # ── Step 6: Save memo to disk ─────────────────────────────────────
        memo_dir  = Path(f"storage/memos/{bill_id}")
        memo_dir.mkdir(parents=True, exist_ok=True)
        memo_path = memo_dir / "memo.md"
        memo_path.write_text(memo_text, encoding="utf-8")
        print(f"[MemoPlugin] Saved memo to {memo_path}")

        return {
            "memo":                    memo_text,
            "memo_path":               str(memo_path),
            "pdf_errors":              pdf_errors,
            "testimonies_char_count":  len(testimonies_text),
            "testifiers_included":     len(testimony_records),
            "api_used":                "claude" if anthropic_key else "openai",
        }
