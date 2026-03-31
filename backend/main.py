"""
Legislative Tool — FastAPI Backend
=====================================
Run with:  uvicorn main:app --reload --port 8000

Endpoints:
  GET  /                         → health check
  GET  /plugins                  → list all available plugins
  POST /run/{plugin_name}        → run a specific plugin
  POST /pipeline/full            → run the entire scrape → transcribe → memo pipeline
  GET  /status/{job_id}          → check status of a long-running job (future use)
"""

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .plugins.registry import REGISTRY

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Legislative Analysis Tool",
    description="Scrapes testimony documents and generates memos using Claude AI",
    version="1.0.0",
)

# Allow the frontend (running on a different port locally) to call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # In production: replace * with your actual domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure storage directories exist when the server starts
for folder in ["storage/downloads", "storage/audio", "storage/transcripts", "storage/memos"]:
    Path(folder).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Request/Response models
# Pydantic models validate that inputs are the right shape before running a plugin.
# If a required field is missing, FastAPI returns a clear 422 error automatically.
# ─────────────────────────────────────────────────────────────────────────────

class PluginInputs(BaseModel):
    inputs: dict


class FullPipelineRequest(BaseModel):
    bill_id: str
    youtube_url: str = ""    # Optional — memo will note if missing


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def health_check():
    """
    Always returns OK. Use this to confirm the server is running.
    Test with: curl http://localhost:8000/
    """
    return {
        "status": "ok",
        "message": "Legislative Analysis Tool is running",
        "plugins_available": list(REGISTRY.keys()),
    }


@app.get("/plugins")
def list_plugins():
    """
    Returns all registered plugins with their names and descriptions.
    The frontend uses this to know what capabilities are available.
    """
    return {
        "plugins": [
            {
                "name": name,
                "description": plugin.description,
                "input_schema": plugin.input_schema(),
            }
            for name, plugin in REGISTRY.items()
        ]
    }


@app.post("/run/{plugin_name}")
async def run_plugin(plugin_name: str, body: PluginInputs):
    """
    Generic endpoint: runs any plugin by name.

    Example — run the scraper:
      POST /run/scraper
      Body: {"inputs": {"bill_id": "3076"}}

    Example — run memo generator:
      POST /run/memo
      Body: {"inputs": {"pdf_paths": ["storage/downloads/..."], "transcript": "...", "bill_id": "3076"}}
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
        # Return the error as a structured response rather than a crash
        return {
            "plugin": plugin_name,
            "result": {"error": f"Unexpected error in plugin: {str(e)}"},
        }


@app.post("/pipeline/full")
async def run_full_pipeline(body: FullPipelineRequest):
    """
    Runs the entire pipeline in sequence:
      1. Scrape testimony PDFs from congress.gov / Floor System
      2. Transcribe the YouTube hearing video (if URL provided)
      3. Generate a memo using Claude AI

    This is the main endpoint the frontend calls when staff click "Generate Memo".

    Example:
      POST /pipeline/full
      Body: {"bill_id": "3076", "youtube_url": "https://youtube.com/watch?v=..."}
    """
    pipeline_result = {
        "bill_id": body.bill_id,
        "steps": {},
        "final_memo": "",
        "errors": [],
    }

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    print(f"[Pipeline] Step 1: Scraping documents for bill {body.bill_id}")
    scraper = REGISTRY["scraper"]
    scrape_result = await scraper.run({"bill_id": body.bill_id})
    pipeline_result["steps"]["scraper"] = scrape_result

    if scrape_result.get("error"):
        pipeline_result["errors"].append(f"Scraper: {scrape_result['error']}")

    pdf_paths = scrape_result.get("downloaded_files", [])
    print(f"[Pipeline] Downloaded {len(pdf_paths)} documents")

    # ── Step 2: Transcribe (optional) ─────────────────────────────────────────
    transcript_text = ""
    if body.youtube_url:
        print(f"[Pipeline] Step 2: Transcribing YouTube hearing")
        transcriber = REGISTRY["transcript"]
        transcript_result = await transcriber.run({
            "youtube_url": body.youtube_url,
            "bill_id": body.bill_id,
        })
        pipeline_result["steps"]["transcript"] = transcript_result

        if transcript_result.get("error"):
            pipeline_result["errors"].append(f"Transcript: {transcript_result['error']}")
        else:
            transcript_text = transcript_result.get("transcript", "")
            print(f"[Pipeline] Transcript: {transcript_result.get('word_count', 0)} words")
    else:
        print("[Pipeline] Step 2: No YouTube URL provided, skipping transcription")
        pipeline_result["steps"]["transcript"] = {"skipped": True, "reason": "No YouTube URL provided"}

    # ── Step 3: Generate Memo ─────────────────────────────────────────────────
    print(f"[Pipeline] Step 3: Generating memo with Claude API")
    memo_generator = REGISTRY["memo"]
    memo_result = await memo_generator.run({
        "pdf_paths": pdf_paths,
        "transcript": transcript_text,
        "bill_id": body.bill_id,
    })
    pipeline_result["steps"]["memo"] = memo_result

    if memo_result.get("error"):
        pipeline_result["errors"].append(f"Memo: {memo_result['error']}")
    else:
        pipeline_result["final_memo"] = memo_result.get("memo", "")

    return pipeline_result