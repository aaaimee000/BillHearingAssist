"""
Memo Generator Plugin
======================
Loads downloaded testimony PDFs + transcript, then calls Claude API
to generate a legislative analysis memo using the specified prompt.
"""

import os
from pathlib import Path
from .base import BasePlugin

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

MEMO_PROMPT_TEMPLATE = """You are a legislative assistant advising a Member of Senate on issues of this Bill.

Write a memo explaining:
1. The position of each testimony and who suggested it
2. What amendments they are looking for, if any
3. Any issues that may require future legislation
4. Any questions raised
5. Themes emerging that demonstrate different approaches by party
6. Recommendations for future action

---

WRITTEN TESTIMONIES:
{testimonies}

---

HEARING TRANSCRIPT:
{transcript}
"""


class MemoPlugin(BasePlugin):
    name = "memo"
    description = "Generates a legislative analysis memo using Claude AI"

    def input_schema(self):
        return {
            "pdf_paths": "list of strings — paths to downloaded testimony PDFs",
            "transcript": "string — the hearing transcript text (can be empty string if unavailable)",
            "bill_id": "string — bill identifier, used for naming the output file",
        }

    async def run(self, inputs: dict) -> dict:
        pdf_paths = inputs.get("pdf_paths", [])
        transcript = inputs.get("transcript", "")
        bill_id = inputs.get("bill_id", "unknown")

        # api_key = os.getenv("ANTHROPIC_API_KEY", "")
        api_key = os.getenv("OPENAI_API_KEY", "")

        if not api_key:
            return {"error": "OPENAI_API_KEY environment variable not set", "memo": ""}

        # Step 1: Extract text from each PDF
        testimonies_text = ""
        pdf_errors = []

        for pdf_path in pdf_paths:
            path = Path(pdf_path)
            if not path.exists():
                pdf_errors.append(f"File not found: {pdf_path}")
                continue
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(path))
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
                testimonies_text += f"\n\n--- TESTIMONY: {path.name} ---\n{text}"
            except ImportError:
                return {
                    "error": "PyMuPDF not installed. Run: pip install pymupdf",
                    "memo": "",
                }
            except Exception as e:
                pdf_errors.append(f"Could not read {pdf_path}: {str(e)}")

        if not testimonies_text and not transcript:
            return {
                "error": "No testimony text or transcript could be extracted. Cannot generate memo.",
                "memo": "",
                "pdf_errors": pdf_errors,
            }

        if not testimonies_text:
            testimonies_text = "(No written testimonies were provided or could be extracted.)"

        if not transcript:
            transcript = "(No hearing transcript was provided.)"

        # Step 2: Build prompt
        prompt = MEMO_PROMPT_TEMPLATE.format(
            testimonies=testimonies_text,
            transcript=transcript,
        )

        # # Step 3: Call Claude API
        # try:
        #     import anthropic
        #     client = anthropic.Anthropic(api_key=api_key)
        #     message = client.messages.create(
        #         model="claude-sonnet-4-6",
        #         max_tokens=4096,
        #         messages=[{"role": "user", "content": prompt}],
        #     )
        #     memo_text = message.content[0].text
        # except ImportError:
        #     return {"error": "Anthropic SDK not installed. Run: pip install anthropic", "memo": ""}
        # except Exception as e:
        #     return {"error": f"Claude API call failed: {str(e)}", "memo": ""}
        
        # Step 3: Call OPENAI API
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            response = client.responses.create(
                model="gpt-5.4-nano",
                input=prompt,
                max_output_tokens=4096,
            )
            memo_text = response.output_text

        except ImportError:
            return {
                "error": "OpenAI SDK not installed. Run: pip install openai",
                "memo": ""
            }
        except Exception as e:
            return {
                "error": f"OpenAI API call failed: {str(e)}",
                "memo": ""
            }

        # Step 4: Save memo to disk
        memo_dir = Path(f"storage/memos/{bill_id}")
        memo_dir.mkdir(parents=True, exist_ok=True)
        memo_path = memo_dir / "memo.md"
        memo_path.write_text(memo_text, encoding="utf-8")

        return {
            "memo": memo_text,
            "memo_path": str(memo_path),
            "pdf_errors": pdf_errors,
            "testimonies_char_count": len(testimonies_text),
        }