# BillHearingAssist
# Legislative Analysis Tool — Developer Guide

## Project structure

```
legislative-tool/
├── backend/
│   ├── main.py                  ← FastAPI server (start here)
│   ├── plugins/
│   │   ├── base.py              ← BasePlugin class (never edit)
│   │   ├── registry.py          ← Add plugins here
│   │   ├── floor_scraper.py     ← Scrapes testimony PDFs
│   │   ├── transcript.py        ← YouTube → transcript
│   │   └── memo_generator.py    ← Calls Claude API
│   ├── storage/                 ← Created automatically on startup
│   └── requirements.txt
├── frontend/
│   └── index.html               ← Open this in a browser
├── tests/
│   └── test_app.py              ← All tests live here
└── pytest.ini
```

---

## Step 1 — One-time setup

```bash
# Create and activate a Python virtual environment
# This keeps your project's packages separate from other projects
python -m venv venv
source venv/bin/activate          # Mac/Linux
venv\Scripts\activate             # Windows

# Install all backend packages
cd backend
pip install -r requirements.txt

# Install the Chromium browser that Playwright uses
playwright install chromium

# Create your .env file (NEVER commit this to git)
# On Mac/Linux:
echo "ANTHROPIC_API_KEY=your-key-here" > .env
echo "FLOOR_SYSTEM_USERNAME=your-username" >> .env
echo "FLOOR_SYSTEM_PASSWORD=your-password" >> .env

# On Windows (Command Prompt):
echo ANTHROPIC_API_KEY=your-key-here > .env
```

---

## Step 2 — Start the backend server

```bash
# Make sure you are in the backend/ folder
cd backend

# Load environment variables and start the server
# On Mac/Linux:
export $(cat .env | xargs) && uvicorn main:app --reload --port 8000

# On Windows (PowerShell):
Get-Content .env | ForEach-Object { $parts = $_ -split '=', 2; [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1]) }
uvicorn main:app --reload --port 8000
```

You should see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

**Quick test that the backend is alive:**
Open a browser and go to: http://localhost:8000/

You should see:
```json
{"status": "ok", "message": "...", "plugins_available": ["scraper", "transcript", "memo"]}
```

---

## Step 3 — Start the frontend

**Do NOT double-click index.html.** Opening HTML as a file:// URL breaks CORS
(the browser blocks requests to localhost from a file:// URL).

Instead, serve it with Python's built-in server:

```bash
# In a NEW terminal window (leave uvicorn running in the first one)
cd frontend
python -m http.server 3000
```

Then open: http://localhost:3000

---

## Step 4 — Run the tests

```bash
# In a NEW terminal window
cd backend

# Run ALL tests with verbose output
pytest ../tests/test_app.py -v

# Run a specific test class
pytest ../tests/test_app.py::TestHealthCheck -v

# Run a specific single test
pytest ../tests/test_app.py::TestHealthCheck::test_server_returns_200_ok -v

# Run tests and see print() output (useful when debugging)
pytest ../tests/test_app.py -v -s
```

**What the output looks like when tests pass:**
```
tests/test_app.py::TestHealthCheck::test_server_returns_200_ok  PASSED
tests/test_app.py::TestHealthCheck::test_health_check_says_ok   PASSED
```

**What the output looks like when a test fails:**
```
FAILED tests/test_app.py::TestHealthCheck::test_server_returns_200_ok
AssertionError: Expected 200 but got 500. Full response: {"detail": "..."}
```
The line after `AssertionError` is your starting point for debugging.

---

## Debugging guide — what to do when something breaks

### The golden rule of debugging as a beginner:
**Read the error message first. All the way to the bottom.**
The last line of a Python traceback is usually the most useful.
The middle lines show you HOW you got there. Work backwards from the bottom.

---

### Common errors and what they mean

#### "ModuleNotFoundError: No module named 'fastapi'"
**What it means:** A package isn't installed.
**Fix:** `pip install -r requirements.txt`
**Why it happens:** You might be in the wrong virtual environment.
Run `which python` — it should show your venv path, not the system Python.

---

#### "Address already in use"
**What it means:** Something else (maybe an old uvicorn) is using port 8000.
**Fix:**
```bash
# Find what's using port 8000:
lsof -i :8000        # Mac/Linux
netstat -ano | findstr :8000   # Windows

# Kill it (replace 12345 with the actual PID):
kill 12345           # Mac/Linux
taskkill /PID 12345 /F   # Windows
```

---

#### "CORS error" in the browser console
**What it means:** The frontend (localhost:3000) is being blocked from talking to the backend (localhost:8000).
**Why it happens:** You opened index.html as a file:// URL instead of through http://localhost:3000
**Fix:** Use `python -m http.server 3000` to serve the frontend.

---

#### Tests fail with "ImportError" or "ModuleNotFoundError"
**What it means:** pytest can't find the backend code.
**Fix:** Make sure you run pytest from the `backend/` folder:
```bash
cd backend && pytest ../tests/test_app.py -v
```

---

#### Test fails with "RuntimeError: no running event loop"
**What it means:** An async test isn't set up correctly.
**Fix:** Make sure pytest.ini has `asyncio_mode = auto` and the test has `@pytest.mark.asyncio`.

---

#### The frontend shows "Cannot reach the backend server"
**Checklist (go through these in order):**
1. Is uvicorn running? Check the terminal where you started it.
2. Is it running on port 8000? Look for `Uvicorn running on http://127.0.0.1:8000`
3. Is there a Python error in the uvicorn terminal? Read it.
4. Try visiting http://localhost:8000/ directly in your browser.
5. Try: `curl http://localhost:8000/` in a terminal. If this works, it's a CORS issue.

---

#### "playwright._impl._errors.TimeoutError"
**What it means:** Playwright waited for an element that never appeared.
**Why it happens:** The selector in the config doesn't match the real HTML.
**Fix:**
1. Set `debug_headless: True` in the scraper config
2. Watch the browser navigate in real time
3. Right-click the element that should appear → Inspect
4. Find the correct CSS selector
5. Update the config

---

#### Claude API returns "authentication_error"
**What it means:** The ANTHROPIC_API_KEY is wrong or not set.
**Fix:**
```bash
echo $ANTHROPIC_API_KEY    # Mac/Linux — should print your key
```
If it's empty, your .env file didn't load. Re-run the export command from Step 2.

---

### How to read a Python traceback

When Python crashes, it prints a "traceback". Example:
```
Traceback (most recent call last):
  File "main.py", line 42, in run_plugin       ← 3rd: where in main.py
    result = await plugin.run(body.inputs)     ← 4th: the line of code
  File "plugins/floor_scraper.py", line 88     ← 2nd: which file
    await page.goto(bill_url)                  ← 1st: what failed
TimeoutError: Timeout 15000ms exceeded         ← START HERE: the actual error
```

**Always read from the bottom up:**
1. Bottom line = what went wrong (`TimeoutError`)
2. Second from bottom = where in your code (`floor_scraper.py line 88`)
3. Work up = how you got there

---

### When you are completely stuck

1. Copy the entire error message (start from "Traceback" or "Error:")
2. Search it on Google / Stack Overflow — most errors are common
3. Add print() statements BEFORE the line that crashes to see what the values are
4. Check that the input data is what you think it is — print it out

The most common cause of bugs is: "the data coming in was not what I expected."
Print your inputs. Always.