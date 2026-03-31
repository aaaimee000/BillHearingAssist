"""
Legislative Analysis Tool — FastAPI Backend
=====================================
Run with:  uvicorn main:app --reload --port 8000
           (run this from inside the backend/ folder)

Endpoints:
  GET  /                   → health check
  GET  /plugins            → list all available plugins
  POST /run/{plugin_name}  → run a specific plugin by name
  POST /pipeline/full      → run the full pipeline (scrape → memo)
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

from plugins.registry import REGISTRY

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Legislative Analysis Tool",
    description="Downloads MGA bill documents and generates memos using Claude AI",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure storage directories exist on startup
for folder in ["storage/downloads", "storage/audio", "storage/transcripts", "storage/memos"]:
    Path(folder).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class PluginInputs(BaseModel):
    inputs: dict


class FullPipelineRequest(BaseModel):
    bill_id:      str           # used for naming output files
    youtube_url:  str  = ""    # kept for backwards compat, not used for MGA
    transcript:   str  = ""    # manual transcript pasted by staff (Option B)
    pdf_paths:    List[str] = []  # if frontend already has paths from scraper step


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def health_check():
    """
    Always returns OK. First thing to check when something breaks.
    Test with: curl http://localhost:8000/
    or just open http://localhost:8000/ in your browser.
    """
    return {
        "status": "ok",
        "message": "Legislative Analysis Tool is running",
        "plugins_available": list(REGISTRY.keys()),
    }


@app.get("/plugins")
def list_plugins():
    """
    Returns all registered plugins with names, descriptions, and what inputs they expect.
    The frontend reads this to confirm the backend is properly configured.
    """
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


@app.post("/run/{plugin_name}")
async def run_plugin(plugin_name: str, body: PluginInputs):
    """
    Generic endpoint: runs any plugin by name.

    Examples:
      POST /run/scraper
      Body: {"inputs": {"bill_number": "hb1532", "session": "2026RS"}}

      POST /run/memo
      Body: {"inputs": {"pdf_paths": ["storage/..."], "transcript": "...", "bill_id": "hb1532"}}
    """
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


@app.post("/pipeline/full")
async def run_full_pipeline(body: FullPipelineRequest):
    """
    Runs the full pipeline. For MGA this means:

      Step 1 — SCRAPE
        Download the bill text PDF (and written testimony PDFs if already available).
        If the frontend already passed pdf_paths from an earlier scraper call,
        skip re-scraping and use those directly.

      Step 2 — TRANSCRIPT
        If a manual transcript was pasted by staff, use that.
        If a youtube_url was given, attempt auto-transcription.
        If neither, skip — the memo will be based on written testimony only.
        This step NEVER blocks the pipeline. Transcript is always optional.

      Step 3 — MEMO
        Call Claude API with all available documents + transcript.
        Always runs, even if step 1 or 2 had problems (graceful fallback).

    IMPORTANT DESIGN DECISION:
      Each step runs regardless of whether the previous step succeeded.
      We collect errors and include them in the response, but we never
      abort early. A memo from partial data is better than no memo.
    """
    bill_id = body.bill_id.strip()

    pipeline_result = {
        "bill_id":    bill_id,
        "steps":      {},
        "final_memo": "",
        "errors":     [],
    }

    # ── Step 1: Scrape / use pre-downloaded PDFs ──────────────────────────────
    # If the frontend already called /run/scraper and has pdf_paths,
    # we skip re-scraping to avoid downloading the same files twice.
    if body.pdf_paths:
        print(f"[Pipeline] Step 1: Using {len(body.pdf_paths)} pre-downloaded PDF(s) from frontend")
        pdf_paths = body.pdf_paths
        pipeline_result["steps"]["scraper"] = {
            "skipped": True,
            "reason":  "PDF paths provided directly by frontend",
            "downloaded_files": pdf_paths,
            "count": len(pdf_paths),
        }
    else:
        print(f"[Pipeline] Step 1: Scraping documents for bill '{bill_id}'")
        scraper      = REGISTRY["scraper"]
        scrape_result = await scraper.run({"bill_number": bill_id, "session": "2026RS"})
        pipeline_result["steps"]["scraper"] = scrape_result

        if scrape_result.get("error"):
            pipeline_result["errors"].append(f"Scraper: {scrape_result['error']}")

        pdf_paths = scrape_result.get("downloaded_files", [])
        print(f"[Pipeline] Downloaded {len(pdf_paths)} document(s)")

    # ── Step 2: Transcript ────────────────────────────────────────────────────
    transcript_text = ""

    if body.transcript:
        # Option B: staff pasted a manual transcript
        transcript_text = body.transcript
        word_count = len(transcript_text.split())
        print(f"[Pipeline] Step 2: Using manual transcript ({word_count} words)")
        pipeline_result["steps"]["transcript"] = {
            "source":     "manual_paste",
            "word_count": word_count,
        }

    elif body.youtube_url:
        # Option A: auto-transcribe from YouTube (kept for backwards compat)
        print(f"[Pipeline] Step 2: Transcribing from YouTube URL")
        transcriber      = REGISTRY["transcript"]
        transcript_result = await transcriber.run({
            "youtube_url": body.youtube_url,
            "bill_id":     bill_id,
        })
        pipeline_result["steps"]["transcript"] = transcript_result

        if transcript_result.get("error"):
            # IMPORTANT: we log the error but do NOT stop the pipeline
            pipeline_result["errors"].append(
                f"Transcript (auto): {transcript_result['error']} — "
                f"memo will be generated from written testimony only"
            )
            print(f"[Pipeline] Transcript failed — continuing without it")
        else:
            transcript_text = transcript_result.get("transcript", "")
            print(f"[Pipeline] Transcript: {transcript_result.get('word_count', 0)} words")

    else:
        # No transcript at all — that's fine, memo still generates
        print("[Pipeline] Step 2: No transcript provided — memo will use written testimony only")
        pipeline_result["steps"]["transcript"] = {
            "skipped": True,
            "reason":  "No transcript provided — memo generated from written testimony only",
        }

    # ── Step 3: Generate Memo ─────────────────────────────────────────────────
    # This always runs. If we have nothing to work with, the memo plugin
    # will return a clear error rather than calling Claude with empty content.
    print(f"[Pipeline] Step 3: Generating memo with Claude API")
    memo_generator = REGISTRY["memo"]
    memo_result    = await memo_generator.run({
        "pdf_paths":  pdf_paths,
        "transcript": transcript_text,
        "bill_id":    bill_id,
    })
    pipeline_result["steps"]["memo"] = memo_result

    if memo_result.get("error"):
        pipeline_result["errors"].append(f"Memo: {memo_result['error']}")
    else:
        pipeline_result["final_memo"] = memo_result.get("memo", "")
        print(f"[Pipeline] Memo generated ({len(pipeline_result['final_memo'])} chars)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"[Pipeline] Complete. Errors: {len(pipeline_result['errors'])}")
    return pipeline_result