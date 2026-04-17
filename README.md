# BillHearingAssist
# Legislative Analysis Tool — Developer Guide

## Project structure

```
BillHearingAssist/
├── backend/
│   ├── main.py                  ← FastAPI server (start here)
│   ├── requirements.txt         ← All Python dependencies
│   ├── plugins/
│   │   ├── base.py              ← BasePlugin class (never edit)
│   │   ├── registry.py          ← Register/swap plugins here
│   │   ├── floor_scraper.py     ← Auto-scrapes testimony PDFs from Senate Floor System (login required)
│   │   ├── mga_scraper.py       ← Public fallback scraper (mgaleg.maryland.gov, no login)
│   │   ├── transcript.py        ← YouTube → auto-transcript via Whisper + yt-dlp
│   │   ├── memo_generator.py    ← Calls Claude or OpenAI to generate legislative memo
│   │   ├── docx_generator.py    ← Converts memo text → formatted Word .docx
│   │   └── drive_uploader.py    ← Uploads memo files to Google Drive (optional)
│   └── storage/                 ← Created automatically on startup
│       ├── downloads/           ← Testimony PDFs scraped from Floor System
│       ├── uploads/             ← Manually uploaded testimony PDFs
│       ├── memos/               ← Generated memos (memo.md + metadata.json per bill)
│       ├── audio/               ← Audio files for Whisper transcription
│       └── transcripts/         ← Saved transcript text files
├── frontend/
│   └── index.html               ← Single-page app (open via http server, not file://)
├── tests/
│   └── test_app.py              ← All tests live here
└── pytest.ini
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Health check — returns server status + registered plugins |
| `GET`  | `/plugins` | List all plugins with their input schemas |
| `POST` | `/run/{plugin_name}` | Run a single plugin by name (body: `{"inputs": {...}}`) |
| `POST` | `/upload-testimony` | Manually upload testimony PDFs (multipart form) |
| `POST` | `/pipeline/full` | Run the full pipeline: scrape → transcript → memo → Drive |
| `GET`  | `/memos` | List all stored memos (newest first) |
| `GET`  | `/memos/{bill_id}` | Retrieve stored memo text + metadata for a specific bill |
| `POST` | `/compare` | AI comparative analysis of two or more previously processed bills |
| `GET`  | `/download/docx/{bill_id}` | Download stored memo as a formatted Word .docx file |

---

## Plugins

### `scraper` — Floor System scraper (`floor_scraper.py`)
- Connects to `https://senatefloor/floor` (requires Senate network + credentials)
- Logs in via CSRF-protected form POST using `FLOOR_SYSTEM_USERNAME` / `FLOOR_SYSTEM_PASSWORD`
- Parses the bill details HTML page for `mgaleg.maryland.gov/ctudata/` PDF links
- Downloads those PDFs directly (they are publicly accessible, no auth needed)
- Returns a `testimony_records` list with name, organization, and position per testifier

**Swap to the public fallback:** In `backend/plugins/registry.py`, comment out `FloorScraperPlugin` and uncomment `MGAScraperPlugin` for offline/dev testing.

### `scraper` (fallback) — MGA public scraper (`mga_scraper.py`)
- Scrapes `mgaleg.maryland.gov` (public, no login required)
- Downloads bill text PDFs and any linked testimony PDFs
- No structured testifier table — position codes inferred from filenames

### `transcript` — YouTube transcriber (`transcript.py`)
- Downloads audio from a YouTube URL using `yt-dlp`
- Runs OpenAI Whisper locally for speech-to-text
- Returns the full transcript text

### `memo` — AI memo generator (`memo_generator.py`)
- Reads downloaded testimony PDFs using PyMuPDF
- Builds a testifier roster from `testimony_records`
- Selects a prompt template based on `memo_style`:
  - `full_analysis` — 6-section professional briefing (default)
  - `quick_summary` — short executive overview
  - `amendments_focus` — deep-dive on every amendment request
  - `opposition_report` — who opposes and exactly why
- Calls Claude (`claude-sonnet-4-6`) or falls back to OpenAI (`gpt-4o`)
- Saves the memo as `storage/memos/{bill_id}/memo.md` with a `metadata.json` sidecar

### `docx_generator` — Word document generator (`docx_generator.py`)
- Converts memo markdown text into a formatted Word `.docx`
- Senate red branding, TO/FROM/DATE/RE header block, bullet lists, bold inline text
- Called automatically by the pipeline and exposed via `GET /download/docx/{bill_id}`

### `drive_uploader` — Google Drive uploader (`drive_uploader.py`)
- Uploads both `.txt` and `.docx` memo files to a configured Google Drive folder
- Silently skipped if `GOOGLE_CREDENTIALS_PATH` and `GOOGLE_DRIVE_FOLDER_ID` are not set in `.env`

---

## Step 1 — One-time setup

```bash
# Create and activate a Python virtual environment
python -m venv venv
source venv/bin/activate          # Mac/Linux
venv\Scripts\activate             # Windows

# Install all backend packages
cd backend
pip install -r requirements.txt

# Install the Chromium browser (used by Playwright, only needed if you extend the scraper)
playwright install chromium

# Create your .env file inside the backend/ folder (NEVER commit this to git)
# Mac/Linux:
echo "ANTHROPIC_API_KEY=your-key-here" > .env
echo "FLOOR_SYSTEM_USERNAME=your-senate-username" >> .env
echo "FLOOR_SYSTEM_PASSWORD=your-senate-password" >> .env

# Windows (Command Prompt):
echo ANTHROPIC_API_KEY=your-key-here > .env
```

### Optional `.env` variables

```ini
# Required for AI memo generation (at least one must be set):
ANTHROPIC_API_KEY=sk-ant-...       # Claude (preferred)
OPENAI_API_KEY=sk-...              # OpenAI fallback

# Required for Floor System auto-scraping (must be on Senate network):
FLOOR_SYSTEM_USERNAME=your-username
FLOOR_SYSTEM_PASSWORD=your-password

# Optional — Google Drive upload after memo generation:
GOOGLE_CREDENTIALS_PATH=/path/to/service-account-key.json
GOOGLE_DRIVE_FOLDER_ID=your-drive-folder-id
```

---

## Step 2 — Start the backend server

```bash
# Run from inside the backend/ folder
cd backend

# Mac/Linux — load .env and start:
export $(cat .env | xargs) && uvicorn main:app --reload --port 8000

# Windows (PowerShell) — load .env and start:
Get-Content .env | ForEach-Object { $parts = $_ -split '=', 2; [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1]) }
uvicorn main:app --reload --port 8000
```

You should see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

**Quick test:** Open http://localhost:8000/ — you should see:
```json
{"status": "ok", "message": "Legislative Analysis Tool is running", "plugins_available": ["scraper", "transcript", "memo"]}
```

---

## Step 3 — Start the frontend

**Do NOT double-click `index.html`.** Opening as `file://` breaks CORS — the browser blocks requests to `localhost` from a `file://` URL.

```bash
# In a NEW terminal (leave uvicorn running in the first one)
cd frontend
python -m http.server 3000
```

Then open: **http://localhost:3000**

### Frontend workflow

The UI is a single-page app with two main sections:

**Analyze tab** — step-by-step pipeline:
1. Enter a bill ID (e.g. `HB1532`) and session (e.g. `2026RS`)
2. The app auto-scrapes testimony from the Senate Floor System — OR — use the **Upload** fallback to manually drag-and-drop PDFs and assign their positions (FAV / FWA / UNF / IMR)
3. Optionally paste a hearing transcript or provide a YouTube URL
4. Choose a memo style and click **Generate Memo**
5. Review the results: donut chart of positions, testifier list, full memo text
6. Download as Word `.docx` or view the Google Drive link (if Drive is configured)

**History tab** — lists all previously generated memos, newest first, with metadata and a Drive link if available.

---

## Step 4 — Run the tests

```bash
# Run from inside the backend/ folder
cd backend

# Run all tests
pytest ../tests/test_app.py -v

# Run a specific test class
pytest ../tests/test_app.py::TestHealthCheck -v

# Run a single test with print() output visible
pytest ../tests/test_app.py::TestHealthCheck::test_server_returns_200_ok -v -s
```

**Tests passing:**
```
tests/test_app.py::TestHealthCheck::test_server_returns_200_ok  PASSED
```

**Tests failing:**
```
FAILED tests/test_app.py::TestHealthCheck::test_server_returns_200_ok
AssertionError: Expected 200 but got 500. Full response: {"detail": "..."}
```

---

## Adding a new plugin

1. Create `backend/plugins/my_plugin.py` — copy the pattern from any existing plugin (subclass `BasePlugin`, implement `name`, `description`, `input_schema()`, and `run()`)
2. Import it in `backend/plugins/registry.py`
3. Add one line to `REGISTRY`

The backend automatically exposes it at `/run/my_plugin`. No changes to `main.py` or the frontend required.

---

## Debugging guide

### Golden rule
**Read the error message first. All the way to the bottom.**
The last line of a Python traceback is the actual error. Work backwards up the stack from there.

---

### Common errors

#### `ModuleNotFoundError: No module named 'fastapi'`
A package isn't installed, or you're in the wrong virtual environment.
```bash
pip install -r requirements.txt
which python   # Mac/Linux — should show your venv path, not system Python
```

---

#### `Address already in use`
Something else (maybe an old uvicorn process) is already on port 8000.
```bash
lsof -i :8000              # Mac/Linux
netstat -ano | findstr :8000   # Windows

kill 12345                 # Mac/Linux (replace with actual PID)
taskkill /PID 12345 /F     # Windows
```

---

#### CORS error in the browser console
You opened `index.html` as a `file://` URL instead of through `http://localhost:3000`.
Fix: use `python -m http.server 3000`.

---

#### `ImportError` or `ModuleNotFoundError` in tests
pytest can't find the backend code. Run pytest from inside the `backend/` folder:
```bash
cd backend && pytest ../tests/test_app.py -v
```

---

#### `RuntimeError: no running event loop`
An async test isn't set up correctly. Check that `pytest.ini` has `asyncio_mode = auto` and the test has `@pytest.mark.asyncio`.

---

#### Frontend shows "Cannot reach the backend server"
Checklist (in order):
1. Is uvicorn running? Check its terminal.
2. Is it on port 8000? Look for `Uvicorn running on http://127.0.0.1:8000`
3. Is there a Python error in the uvicorn terminal? Read it.
4. Visit http://localhost:8000/ directly in the browser.
5. `curl http://localhost:8000/` — if this works, it's a CORS issue.

---

#### Floor System login fails
- Verify `FLOOR_SYSTEM_USERNAME` and `FLOOR_SYSTEM_PASSWORD` in `backend/.env`
- Confirm you are on the Senate network (VPN or on-site)
- If Floor System is unreachable, switch to the manual upload fallback or the MGA public scraper

---

#### `claude API returns "authentication_error"`
The `ANTHROPIC_API_KEY` is wrong or not loaded.
```bash
echo $ANTHROPIC_API_KEY    # Mac/Linux — should print your key
```
If empty, re-run the `export` command from Step 2, or add the key directly to your shell environment.

---

#### Google Drive upload is skipped
This is expected behaviour when Drive is not configured. Set `GOOGLE_CREDENTIALS_PATH` and `GOOGLE_DRIVE_FOLDER_ID` in `.env` and share the Drive folder with the service account email.

---

### How to read a Python traceback

```
Traceback (most recent call last):
  File "main.py", line 42, in run_plugin       ← 3rd: where in main.py
    result = await plugin.run(body.inputs)     ← 4th: the line of code
  File "plugins/floor_scraper.py", line 88     ← 2nd: which file
    await page.goto(bill_url)                  ← 1st: what failed
TimeoutError: Timeout 15000ms exceeded         ← START HERE
```

Always read bottom-up: error type → file/line → call chain.

---

### When completely stuck

1. Copy the entire error (from "Traceback" or "Error:" to the end)
2. Search Google / Stack Overflow — most errors are common
3. Add `print()` statements before the crashing line to inspect values
4. The most common cause: the data coming in was not what you expected — print your inputs
