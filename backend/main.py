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

from backend.plugins.registry import REGISTRY

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Legislative Analysis Tool",
    description="Downloads MGA Floor System testimony and generates memos using AI",
    version="3.0.0",
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
    bill_id:           str              # e.g. "HB1532"
    youtube_url:       str  = ""        # kept for backwards compat
    transcript:        str  = ""        # manual transcript paste (optional)
    pdf_paths:         List[str] = []   # pre-downloaded paths (skip re-scraping)
    testimony_records: List[dict] = []  # pre-scraped metadata (skip re-scraping)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def health_check():
    return {
        "status":             "ok",
        "message":            "Legislative Analysis Tool is running",
        "plugins_available":  list(REGISTRY.keys()),
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


@app.post("/pipeline/full")
async def run_full_pipeline(body: FullPipelineRequest):
    """
    Full pipeline:
      Step 1 — SCRAPE
        Logs into Floor System, reads Committee Testimony tab,
        downloads all PDFs, returns testimony_records metadata.
        Skipped if frontend already provides pdf_paths + testimony_records.

      Step 2 — TRANSCRIPT (optional)
        Uses manual paste if provided. YouTube auto-transcription if URL given.
        If neither, memo is built from testimony only — that's fine.

      Step 3 — MEMO
        Passes PDFs + testimony_records + transcript to memo generator.
        Always runs. Returns error message if truly nothing is available.
    """
    bill_id = body.bill_id.strip().upper()

    pipeline_result = {
        "bill_id":    bill_id,
        "steps":      {},
        "final_memo": "",
        "errors":     [],
    }

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    if body.pdf_paths and body.testimony_records:
        # Frontend already ran the scraper — use what it has, don't re-scrape
        print(f"[Pipeline] Step 1: Using {len(body.pdf_paths)} pre-downloaded files from frontend")
        pdf_paths         = body.pdf_paths
        testimony_records = body.testimony_records
        pipeline_result["steps"]["scraper"] = {
            "skipped":         True,
            "reason":          "PDF paths and testimony records provided by frontend",
            "downloaded_files": pdf_paths,
            "count":           len(pdf_paths),
        }
    else:
        print(f"[Pipeline] Step 1: Scraping Floor System for bill '{bill_id}'")
        scraper      = REGISTRY["scraper"]
        # bill_id key used — floor_scraper.run() accepts both 'bill_id' and 'bill_number'
        scrape_result = await scraper.run({"bill_id": bill_id})
        pipeline_result["steps"]["scraper"] = scrape_result

        if scrape_result.get("error"):
            pipeline_result["errors"].append(f"Scraper: {scrape_result['error']}")

        pdf_paths         = scrape_result.get("downloaded_files", [])
        testimony_records = scrape_result.get("testimony_records", [])
        print(f"[Pipeline] Downloaded {len(pdf_paths)} PDFs, {len(testimony_records)} testifiers recorded")

    # ── Step 2: Transcript ────────────────────────────────────────────────────
    transcript_text = ""

    if body.transcript:
        transcript_text = body.transcript
        word_count = len(transcript_text.split())
        print(f"[Pipeline] Step 2: Manual transcript ({word_count} words)")
        pipeline_result["steps"]["transcript"] = {
            "source":     "manual_paste",
            "word_count": word_count,
        }
    elif body.youtube_url:
        print(f"[Pipeline] Step 2: Auto-transcribing YouTube URL")
        transcriber       = REGISTRY.get("transcript")
        if not transcriber:
            pipeline_result["errors"].append("Transcript: 'transcript' plugin not registered")
            pipeline_result["steps"]["transcript"] = {"skipped": True, "reason": "Plugin not available"}
        else:
            transcript_result = await transcriber.run({
                "youtube_url": body.youtube_url,
                "bill_id":     bill_id,
            })
            pipeline_result["steps"]["transcript"] = transcript_result
            if transcript_result.get("error"):
                pipeline_result["errors"].append(
                    f"Transcript: {transcript_result['error']} — continuing without transcript"
                )
            else:
                transcript_text = transcript_result.get("transcript", "")
    else:
        print("[Pipeline] Step 2: No transcript — memo will use written testimony only")
        pipeline_result["steps"]["transcript"] = {
            "skipped": True,
            "reason":  "No transcript provided",
        }

    # ── Step 3: Generate Memo ─────────────────────────────────────────────────
    print(f"[Pipeline] Step 3: Generating memo")
    memo_generator = REGISTRY["memo"]
    memo_result    = await memo_generator.run({
        "pdf_paths":         pdf_paths,
        "testimony_records": testimony_records,   # ← KEY: rich metadata passed through
        "transcript":        transcript_text,
        "bill_id":           bill_id,
    })
    pipeline_result["steps"]["memo"] = memo_result

    if memo_result.get("error"):
        pipeline_result["errors"].append(f"Memo: {memo_result['error']}")
    else:
        pipeline_result["final_memo"]  = memo_result.get("memo", "")
        pipeline_result["testifiers"]  = len(testimony_records)
        pipeline_result["api_used"]    = memo_result.get("api_used", "unknown")
        print(f"[Pipeline] Memo: {len(pipeline_result['final_memo'])} chars via {pipeline_result['api_used']}")

    print(f"[Pipeline] Complete. {len(pipeline_result['errors'])} error(s)")
    return pipeline_result

# Note: currently when putting in hb1532, the url link is not shown correctly as the floor scraper website url, 

# the updated index html is missing a function where you need to get the written testimony from the corrected url, 
#and you would also need to get some functions to get the pdf links and download the pdfs, and then you can use the 
#memo generator to generate the memo. The transcript plugin is optional, you can paste the transcript in the text inbox

#1. the url is incorrect 2. the written testimony download function is mssing, the actual conversation is missing, 
#4. the model comparison of work too should be added into the AI report and shown it to katie 

