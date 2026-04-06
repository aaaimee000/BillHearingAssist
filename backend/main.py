"""
Legislative Analysis Tool — FastAPI Backend
============================================
Run with:  uvicorn main:app --reload --port 8000
           (run from inside the backend/ folder)

Endpoints:
  GET  /                          → health check
  GET  /plugins                   → list plugins
  POST /run/{plugin_name}         → run a specific plugin
  POST /upload-testimony          → manually upload testimony PDFs
  POST /pipeline/full             → full pipeline (scrape → memo)
  GET  /memos                     → list all stored memos
  GET  /memos/{bill_id}           → retrieve a specific stored memo
  POST /compare                   → AI comparison of two or more bills
  GET  /download/docx/{bill_id}   → download memo as Word .docx
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.plugins.registry import REGISTRY
from backend.plugins.drive_uploader import upload_to_drive
from backend.plugins.docx_generator import generate_docx_buffer

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Legislative Analysis Tool",
    description="Downloads MGA Floor System testimony and generates memos using AI",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure storage directories exist on startup
for folder in [
    "storage/downloads", "storage/audio", "storage/transcripts",
    "storage/memos", "storage/uploads",
]:
    Path(folder).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class PluginInputs(BaseModel):
    inputs: dict


class FullPipelineRequest(BaseModel):
    bill_id:           str
    session:           str       = "2026RS"
    youtube_url:       str       = ""
    transcript:        str       = ""
    pdf_paths:         List[str] = []
    testimony_records: List[dict] = []
    memo_style:        str       = "full_analysis"


class CompareBillsRequest(BaseModel):
    bill_ids: List[str]


# ─────────────────────────────────────────────────────────────────────────────
# Health & plugin discovery
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def health_check():
    return {
        "status":            "ok",
        "message":           "Legislative Analysis Tool is running",
        "plugins_available": list(REGISTRY.keys()),
    }


@app.get("/plugins")
def list_plugins():
    return {
        "plugins": [
            {
                "name":         name,
                "description":  plugin.description,
                "input_schema": plugin.input_schema(),
            }
            for name, plugin in REGISTRY.items()
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Run individual plugin
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/run/{plugin_name}")
async def run_plugin(plugin_name: str, body: PluginInputs):
    plugin = REGISTRY.get(plugin_name)
    if not plugin:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_name}' not found. Available: {list(REGISTRY.keys())}",
        )
    try:
        result = await plugin.run(body.inputs)
        return {"plugin": plugin_name, "result": result}
    except Exception as e:
        return {
            "plugin": plugin_name,
            "result": {"error": f"Unexpected error in plugin: {str(e)}"},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Manual testimony upload
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/upload-testimony")
async def upload_testimony(
    bill_id:   str             = Form("UPLOAD"),
    session:   str             = Form("2026RS"),
    positions: str             = Form("[]"),   # JSON array of position codes, one per file
    files:     List[UploadFile] = File(...),
):
    """
    Accept manually uploaded testimony PDFs.
    'positions' is a JSON array like ["FAV","UNF","IMR"] — one entry per uploaded file,
    set by the user via the position dropdown in the upload UI.
    This is what makes the donut chart and filters accurate on the Review screen.
    """
    bill_id    = re.sub(r"[^\w-]", "", bill_id.strip()).upper() or "UPLOAD"
    upload_dir = Path(f"storage/uploads/{bill_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)

    POSITION_LABELS = {
        "FAV": "In Favor",
        "FWA": "In Favor with Amendments",
        "UNF": "Opposed",
        "IMR": "Informational",
    }

    # Parse the user-selected positions from the frontend dropdowns
    try:
        pos_list = json.loads(positions)  # e.g. ["FAV", "UNF", "IMR"]
    except Exception:
        pos_list = []

    saved_paths       = []
    testimony_records = []
    errors            = []

    for i, upload in enumerate(files):
        try:
            content   = await upload.read()
            safe_name = re.sub(r"[^\w._-]", "_", upload.filename or f"testimony_{i:02d}.pdf")
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"

            file_path = upload_dir / safe_name
            file_path.write_bytes(content)
            saved_paths.append(str(file_path))

            # Priority order for position:
            #   1. User-selected via UI dropdown (most accurate)
            #   2. Filename convention fallback (e.g. "JohnSmith_FAV.pdf")
            #   3. Default to IMR
            position = pos_list[i] if i < len(pos_list) else ""
            if position not in POSITION_LABELS:
                # Fallback: scan filename for position code
                fname_upper = Path(safe_name).stem.upper()
                position = next(
                    (code for code in POSITION_LABELS if code in fname_upper),
                    "IMR"
                )

            position_label = POSITION_LABELS.get(position, "Informational")

            testimony_records.append({
                "name":           Path(safe_name).stem,
                "organization":   "—",
                "position":       position,
                "position_label": position_label,
                "testimony_type": "Written",
                "pdf_filename":   safe_name,
                "pdf_url":        "",
                "source":         "manual_upload",
            })

        except Exception as e:
            errors.append(f"Error saving {upload.filename}: {str(e)}")

    print(f"[Upload] {len(saved_paths)} PDFs saved for {bill_id}")

    return {
        "downloaded_files":  saved_paths,
        "testimony_records": testimony_records,
        "documents_found":   [],
        "count":             len(saved_paths),
        "total_testifiers":  len(testimony_records),
        "bill_id":           bill_id,
        "session":           session,
        "errors":            errors,
        "source":            "manual_upload",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline: scrape → transcript → memo
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/pipeline/full")
async def run_full_pipeline(body: FullPipelineRequest):
    """
    Steps:
      1. SCRAPE  — download PDFs + testifier metadata (skipped if already provided)
      2. TRANSCRIPT — manual paste or YouTube auto-transcribe (optional)
      3. MEMO — AI analysis with selected style
      4. DRIVE — upload memo + docx to Google Drive (if configured)
    """
    bill_id = body.bill_id.strip().upper()

    pipeline_result = {
        "bill_id":    bill_id,
        "session":    body.session,
        "steps":      {},
        "final_memo": "",
        "errors":     [],
        "drive":      {},
    }

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    if body.pdf_paths and body.testimony_records:
        print(f"[Pipeline] Step 1: Using {len(body.pdf_paths)} pre-provided file(s)")
        pdf_paths         = body.pdf_paths
        testimony_records = body.testimony_records
        pipeline_result["steps"]["scraper"] = {
            "skipped":          True,
            "reason":           "PDF paths and testimony records provided by caller",
            "downloaded_files": pdf_paths,
            "count":            len(pdf_paths),
        }
    else:
        print(f"[Pipeline] Step 1: Scraping for '{bill_id}'")
        scraper       = REGISTRY["scraper"]
        scrape_result = await scraper.run({"bill_id": bill_id, "session": body.session})
        pipeline_result["steps"]["scraper"] = scrape_result

        if scrape_result.get("error"):
            pipeline_result["errors"].append(f"Scraper: {scrape_result['error']}")

        pdf_paths         = scrape_result.get("downloaded_files", [])
        testimony_records = scrape_result.get("testimony_records", [])
        print(f"[Pipeline] {len(pdf_paths)} PDF(s), {len(testimony_records)} testifier(s)")

    # ── Step 2: Transcript ────────────────────────────────────────────────────
    transcript_text = ""

    if body.transcript:
        transcript_text = body.transcript
        wc = len(transcript_text.split())
        print(f"[Pipeline] Step 2: Manual transcript ({wc} words)")
        pipeline_result["steps"]["transcript"] = {"source": "manual_paste", "word_count": wc}

    elif body.youtube_url:
        print(f"[Pipeline] Step 2: Auto-transcribing YouTube URL")
        transcriber = REGISTRY.get("transcript")
        if not transcriber:
            pipeline_result["errors"].append("Transcript plugin not registered")
            pipeline_result["steps"]["transcript"] = {"skipped": True}
        else:
            tr = await transcriber.run({"youtube_url": body.youtube_url, "bill_id": bill_id})
            pipeline_result["steps"]["transcript"] = tr
            if tr.get("error"):
                pipeline_result["errors"].append(f"Transcript: {tr['error']}")
            else:
                transcript_text = tr.get("transcript", "")
    else:
        print("[Pipeline] Step 2: No transcript — using written testimony only")
        pipeline_result["steps"]["transcript"] = {"skipped": True, "reason": "Not provided"}

    # ── Step 3: Generate Memo ─────────────────────────────────────────────────
    print(f"[Pipeline] Step 3: Generating memo (style={body.memo_style})")
    memo_generator = REGISTRY["memo"]
    memo_result    = await memo_generator.run({
        "pdf_paths":         pdf_paths,
        "testimony_records": testimony_records,
        "transcript":        transcript_text,
        "bill_id":           bill_id,
        "session":           body.session,
        "memo_style":        body.memo_style,
    })
    pipeline_result["steps"]["memo"] = memo_result

    if memo_result.get("error"):
        pipeline_result["errors"].append(f"Memo: {memo_result['error']}")
    else:
        memo_text = memo_result.get("memo", "")
        pipeline_result["final_memo"] = memo_text
        pipeline_result["testifiers"] = len(testimony_records)
        pipeline_result["api_used"]   = memo_result.get("api_used", "unknown")
        pipeline_result["memo_style"] = memo_result.get("memo_style", body.memo_style)
        print(f"[Pipeline] Memo: {len(memo_text)} chars via {pipeline_result['api_used']}")

        # ── Step 4: Google Drive upload ───────────────────────────────────
        memo_text_bytes = memo_text.encode("utf-8")
        filename_base   = f"memo_{bill_id}_{body.session}"

        # Upload .txt
        drive_result = upload_to_drive(
            content   = memo_text_bytes,
            filename  = f"{filename_base}.txt",
            mime_type = "text/plain",
        )

        # Upload .docx (generate on the fly)
        try:
            docx_buf = generate_docx_buffer(
                memo_text       = memo_text,
                bill_id         = bill_id,
                session         = body.session,
                testifier_count = len(testimony_records),
                memo_style      = body.memo_style,
            )
            docx_result = upload_to_drive(
                content   = docx_buf.read(),
                filename  = f"{filename_base}.docx",
                mime_type = (
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"
                ),
            )
        except Exception as e:
            docx_result = {"error": str(e)}

        pipeline_result["drive"] = {
            "txt":  drive_result,
            "docx": docx_result,
        }

        # Persist drive URL into metadata.json so history can show the link
        drive_url = drive_result.get("url", "") or docx_result.get("url", "")
        if drive_url:
            meta_path = Path(f"storage/memos/{bill_id}/metadata.json")
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    meta["drive_url"] = drive_url
                    meta_path.write_text(json.dumps(meta, indent=2))
                except Exception:
                    pass

    print(f"[Pipeline] Complete. {len(pipeline_result['errors'])} error(s).")
    return pipeline_result


# ─────────────────────────────────────────────────────────────────────────────
# Bill History
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/memos")
def list_memos():
    """Return metadata for all stored memos, newest first."""
    memos_dir = Path("storage/memos")
    results   = []

    if not memos_dir.exists():
        return {"memos": []}

    for bill_dir in sorted(memos_dir.iterdir(), reverse=True):
        if not bill_dir.is_dir():
            continue

        memo_path = bill_dir / "memo.md"
        meta_path = bill_dir / "metadata.json"

        if not memo_path.exists():
            continue

        metadata = {}
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        try:
            preview = memo_path.read_text(encoding="utf-8")[:220].replace("\n", " ").strip()
        except Exception:
            preview = ""

        results.append({
            "bill_id":      metadata.get("bill_id",    bill_dir.name),
            "session":      metadata.get("session",    ""),
            "generated_at": metadata.get("generated_at", ""),
            "testifiers":   metadata.get("testifiers", 0),
            "pdf_count":    metadata.get("pdf_count",  0),
            "api_used":     metadata.get("api_used",   ""),
            "memo_style":   metadata.get("memo_style", "full_analysis"),
            "drive_url":    metadata.get("drive_url",  ""),
            "preview":      preview,
        })

    return {"memos": results}


@app.get("/memos/{bill_id}")
def get_memo(bill_id: str):
    """Return the stored memo text and metadata for a specific bill."""
    bill_id   = re.sub(r"[^\w-]", "", bill_id.strip()).upper()
    memo_path = Path(f"storage/memos/{bill_id}/memo.md")
    meta_path = Path(f"storage/memos/{bill_id}/metadata.json")

    if not memo_path.exists():
        raise HTTPException(status_code=404, detail=f"No memo found for {bill_id}")

    memo_text = memo_path.read_text(encoding="utf-8")
    metadata  = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {"bill_id": bill_id, "memo": memo_text, "metadata": metadata}


# ─────────────────────────────────────────────────────────────────────────────
# Multi-Bill Comparison
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/compare")
async def compare_bills(body: CompareBillsRequest):
    """
    Load stored memos for each bill and ask the AI to write a comparative analysis.
    Both bills must have been processed previously (memos exist on disk).
    """
    if len(body.bill_ids) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 bill IDs to compare.")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key    = os.getenv("OPENAI_API_KEY", "")
    if not anthropic_key and not openai_key:
        raise HTTPException(status_code=500, detail="No AI API key configured in .env")

    bills = []
    for raw_id in body.bill_ids:
        bid       = re.sub(r"[^\w-]", "", raw_id.strip()).upper()
        memo_path = Path(f"storage/memos/{bid}/memo.md")
        meta_path = Path(f"storage/memos/{bid}/metadata.json")

        if not memo_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No stored memo for {bid}. Run the full pipeline for this bill first.",
            )

        metadata = {}
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        bills.append({
            "bill_id": bid,
            "session": metadata.get("session", ""),
            "memo":    memo_path.read_text(encoding="utf-8"),
        })

    compare_prompt = f"""You are a senior legislative analyst for the Maryland Senate.

You have been given analysis memos for {len(bills)} related bills. Write a COMPARATIVE ANALYSIS covering:

1. SHARED THEMES
   What issues, concerns, or arguments appear across multiple bills?

2. SHARED TESTIFIERS
   Which organisations or individuals testified on more than one bill, and what were their positions?

3. CONFLICTING POSITIONS
   Where do the same stakeholder groups take different stances across bills?

4. LEGISLATIVE SYNERGIES
   Which provisions complement each other? Could they be combined or coordinated?

5. KEY DIFFERENCES
   What distinguishes each bill in terms of approach, stakeholder support, and scope?

6. STRATEGIC RECOMMENDATIONS
   Given the full picture, what is the strategic path forward for the Senate?

---

BILLS BEING COMPARED:

"""
    for b in bills:
        header = f"BILL: {b['bill_id']}"
        if b["session"]:
            header += f" ({b['session']})"
        compare_prompt += f"\n{'='*60}\n{header}\n{'='*60}\n{b['memo']}\n\n"

    comparison_text = ""
    api_used        = ""

    if anthropic_key:
        try:
            import anthropic
            client  = anthropic.Anthropic(api_key=anthropic_key)
            message = client.messages.create(
                model      = "claude-sonnet-4-6",
                max_tokens = 4096,
                messages   = [{"role": "user", "content": compare_prompt}],
            )
            comparison_text = message.content[0].text
            api_used        = "claude"
            print(f"[Compare] Claude returned {len(comparison_text)} chars")
        except Exception as e:
            if not openai_key:
                raise HTTPException(status_code=500, detail=f"Claude API failed: {str(e)}")
            print(f"[Compare] Claude failed ({e}), trying OpenAI…")

    if not comparison_text and openai_key:
        try:
            from openai import OpenAI
            client   = OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model    = "gpt-4o",
                max_tokens = 4096,
                messages = [{"role": "user", "content": compare_prompt}],
            )
            comparison_text = response.choices[0].message.content
            api_used        = "openai"
            print(f"[Compare] OpenAI returned {len(comparison_text)} chars")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OpenAI API failed: {str(e)}")

    return {
        "bill_ids":   [b["bill_id"] for b in bills],
        "comparison": comparison_text,
        "api_used":   api_used,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Word (.docx) download
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/download/docx/{bill_id}")
def download_docx(bill_id: str):
    """Generate and stream a .docx Word document from the stored memo."""
    bill_id   = re.sub(r"[^\w-]", "", bill_id.strip()).upper()
    memo_path = Path(f"storage/memos/{bill_id}/memo.md")
    meta_path = Path(f"storage/memos/{bill_id}/metadata.json")

    if not memo_path.exists():
        raise HTTPException(status_code=404, detail=f"No memo found for {bill_id}")

    memo_text = memo_path.read_text(encoding="utf-8")
    metadata  = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        buf = generate_docx_buffer(
            memo_text       = memo_text,
            bill_id         = bill_id,
            session         = metadata.get("session", ""),
            testifier_count = metadata.get("testifiers", 0),
            memo_style      = metadata.get("memo_style", "full_analysis"),
        )
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="python-docx not installed. Run: pip install python-docx",
        )

    session      = metadata.get("session", "")
    safe_session = re.sub(r"[^\w]", "", session)
    filename     = f"memo_{bill_id}_{safe_session}.docx" if safe_session else f"memo_{bill_id}.docx"

    return StreamingResponse(
        buf,
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
