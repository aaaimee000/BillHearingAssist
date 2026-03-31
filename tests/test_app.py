"""
Test Suite for the Legislative Analysis Tool
=============================================

HOW TO RUN:
  cd backend
  pytest ../tests/test_app.py -v

  -v means "verbose" — shows each test name and pass/fail individually
  Add -s to also see print() output: pytest -v -s

WHAT IS A TEST CASE?
  A test case answers ONE specific question: "If I do X, does the app do Y?"
  Good test cases are:
    1. SMALL  — test one thing at a time, not five at once
    2. NAMED  — the test name should read like a sentence describing what's expected
    3. INDEPENDENT — each test should not depend on another test having run first

  The three parts of every test (called "Arrange / Act / Assert"):
    1. Arrange — set up the inputs and any fake data you need
    2. Act     — call the function or endpoint you're testing
    3. Assert  — check the result is what you expect

  If an assertion fails, pytest tells you exactly which line broke and what the
  actual vs expected values were. That's your debugging starting point.

CATEGORIES IN THIS FILE:
  1. Health / connectivity tests       — is the server reachable?
  2. Plugin unit tests                 — does each plugin handle inputs correctly?
  3. API endpoint tests                — do the FastAPI routes work?
  4. Pipeline integration tests        — does the full scrape → memo flow work end to end?
  5. Edge case tests                   — what happens with bad/empty/weird inputs?
"""

import pytest
import asyncio
import sys
import os
from pathlib import Path

# Add the backend folder to the Python path so we can import from it
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# httpx is the HTTP client we use to test FastAPI without running a real server
from httpx import AsyncClient, ASGITransport


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# A "fixture" is a piece of setup code that pytest runs before your tests.
# The @pytest.fixture decorator marks them. Tests can "use" a fixture by
# putting its name as a function argument.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """
    Imports the FastAPI app. We put this in a fixture so every test gets
    a clean import without any shared state between tests.
    """
    from main import app
    return app


@pytest.fixture
async def client(app):
    """
    Creates a test HTTP client that talks to the FastAPI app *directly*,
    without needing a running server. This is how we test endpoints.

    AsyncClient with ASGITransport = fake network, real app logic.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def sample_pdf(tmp_path):
    """
    Creates a tiny fake PDF file for testing.
    We don't need a real PDF — we just need a file that exists so the
    memo plugin can try to open it (and give us a useful error).

    tmp_path is a pytest built-in fixture that creates a temporary folder
    that gets deleted after each test automatically.
    """
    pdf_path = tmp_path / "test_testimony.pdf"
    # Write minimal PDF bytes — enough to be a real (empty) PDF
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return str(pdf_path)


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 1: HEALTH / CONNECTIVITY
# These are the FIRST tests to run. If these fail, nothing else will work.
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthCheck:
    """
    INTERN NOTE: Always write health check tests first.
    If the server can't start, every other test will fail with confusing errors.
    A clear "server is broken" failure is much easier to debug.
    """

    @pytest.mark.asyncio
    async def test_server_returns_200_ok(self, client):
        """
        Arrange: nothing (health check needs no input)
        Act: GET /
        Assert: HTTP status is 200 (means "success")

        If this fails: uvicorn probably can't start — check main.py for syntax errors
        """
        response = await client.get("/")
        assert response.status_code == 200, \
            f"Expected 200 but got {response.status_code}. Full response: {response.text}"

    @pytest.mark.asyncio
    async def test_health_check_says_ok(self, client):
        """
        Assert: the response JSON has status = "ok"
        """
        response = await client.get("/")
        data = response.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_lists_plugins(self, client):
        """
        Assert: the health check tells us which plugins exist.
        This confirms the registry was imported successfully.
        """
        response = await client.get("/")
        data = response.json()
        assert "plugins_available" in data, "Response should include plugins_available list"
        assert len(data["plugins_available"]) > 0, "Should have at least one plugin registered"

    @pytest.mark.asyncio
    async def test_plugins_endpoint_returns_list(self, client):
        """
        The /plugins endpoint should return info about every registered plugin.
        """
        response = await client.get("/plugins")
        assert response.status_code == 200
        data = response.json()
        assert "plugins" in data
        plugin_names = [p["name"] for p in data["plugins"]]
        assert "scraper" in plugin_names,    "scraper plugin should be registered"
        assert "transcript" in plugin_names, "transcript plugin should be registered"
        assert "memo" in plugin_names,       "memo plugin should be registered"


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 2: PLUGIN UNIT TESTS
# These test each plugin's run() method directly, without going through HTTP.
# This is faster and pinpoints exactly which plugin has a problem.
# ─────────────────────────────────────────────────────────────────────────────

class TestScraperPlugin:
    """
    INTERN NOTE: We test the plugin's VALIDATION logic (does it reject bad input?)
    without actually running Playwright, which needs a browser installed.
    
    How to think about what to test for a plugin:
      - What happens if a REQUIRED input is missing?
      - What happens if an input is the WRONG TYPE?
      - Does the output have ALL the keys the rest of the system expects?
    """

    @pytest.mark.asyncio
    async def test_scraper_rejects_empty_bill_id(self):
        """
        If bill_id is empty, the scraper should return an error dict, NOT crash.
        """
        from plugins.floor_scraper import FloorScraperPlugin
        plugin = FloorScraperPlugin()
        result = await plugin.run({"bill_id": ""})

        # The plugin should return a dict with "error" key, not raise an exception
        assert "error" in result, f"Expected error key in result, got: {result}"
        assert result["error"] != "", "Error message should not be empty"

    @pytest.mark.asyncio
    async def test_scraper_rejects_missing_bill_id(self):
        """
        If bill_id is not provided at all, same behavior expected.
        """
        from plugins.floor_scraper import FloorScraperPlugin
        plugin = FloorScraperPlugin()
        result = await plugin.run({})  # No bill_id at all

        assert "error" in result
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_scraper_output_has_required_keys(self):
        """
        Even when there's an error, the output dict must have these keys.
        The pipeline and frontend depend on these keys existing.
        This is called testing the "contract" of the plugin.
        """
        from plugins.floor_scraper import FloorScraperPlugin
        plugin = FloorScraperPlugin()
        result = await plugin.run({"bill_id": ""})

        required_keys = ["downloaded_files", "count"]
        for key in required_keys:
            assert key in result, f"Result is missing required key: '{key}'"


class TestTranscriptPlugin:

    @pytest.mark.asyncio
    async def test_transcript_rejects_empty_url(self):
        from plugins.transcript import TranscriptPlugin
        plugin = TranscriptPlugin()
        result = await plugin.run({"youtube_url": "", "bill_id": "test"})

        assert "error" in result
        assert result["transcript"] == ""

    @pytest.mark.asyncio
    async def test_transcript_rejects_non_youtube_url(self):
        """
        If someone pastes a random URL (not YouTube), the plugin should
        reject it with a clear error rather than crashing or silently failing.
        """
        from plugins.transcript import TranscriptPlugin
        plugin = TranscriptPlugin()
        result = await plugin.run({
            "youtube_url": "https://www.google.com",
            "bill_id": "test"
        })

        assert "error" in result
        assert "youtube" in result["error"].lower() or "url" in result["error"].lower(), \
            f"Error message should mention YouTube or URL, got: {result['error']}"

    @pytest.mark.asyncio
    async def test_transcript_rejects_missing_url(self):
        from plugins.transcript import TranscriptPlugin
        plugin = TranscriptPlugin()
        result = await plugin.run({"bill_id": "test"})  # no youtube_url key

        assert "error" in result

    @pytest.mark.asyncio
    async def test_transcript_output_has_required_keys(self):
        from plugins.transcript import TranscriptPlugin
        plugin = TranscriptPlugin()
        result = await plugin.run({"youtube_url": "", "bill_id": "test"})

        # These keys must always exist even on failure
        assert "transcript" in result
        assert "transcript_path" in result


class TestMemoPlugin:

    @pytest.mark.asyncio
    async def test_memo_fails_gracefully_with_no_api_key(self, monkeypatch):
        """
        "monkeypatch" is a pytest feature that lets you temporarily change
        environment variables or function behavior during a test.
        Here we remove the API key to simulate a misconfigured environment.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        from plugins.memo_generator import MemoPlugin
        plugin = MemoPlugin()
        result = await plugin.run({
            "pdf_paths": [],
            "transcript": "Some transcript text",
            "bill_id": "test"
        })

        assert "error" in result
        assert "api_key" in result["error"].lower() or "anthropic" in result["error"].lower(), \
            f"Error should mention API key, got: {result['error']}"

    @pytest.mark.asyncio
    async def test_memo_fails_gracefully_with_no_content(self, monkeypatch):
        """
        If there are no PDFs AND no transcript, the plugin should return a
        useful error rather than sending an empty prompt to Claude.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-testing")

        from plugins.memo_generator import MemoPlugin
        plugin = MemoPlugin()
        result = await plugin.run({
            "pdf_paths": [],
            "transcript": "",
            "bill_id": "test"
        })

        assert "error" in result, f"Should error when both PDF list and transcript are empty. Got: {result}"

    @pytest.mark.asyncio
    async def test_memo_output_has_required_keys(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from plugins.memo_generator import MemoPlugin
        plugin = MemoPlugin()
        result = await plugin.run({"pdf_paths": [], "transcript": "", "bill_id": "test"})

        assert "memo" in result


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 3: API ENDPOINT TESTS
# These test the FastAPI routes via HTTP, not the plugin internals.
# ─────────────────────────────────────────────────────────────────────────────

class TestAPIEndpoints:

    @pytest.mark.asyncio
    async def test_run_unknown_plugin_returns_404(self, client):
        """
        If someone calls /run/nonexistent, they should get a clear 404 error.
        Not a crash, not a 500 error, not an empty response.
        """
        response = await client.post(
            "/run/nonexistent_plugin",
            json={"inputs": {}}
        )
        assert response.status_code == 404, \
            f"Expected 404 for unknown plugin, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_run_plugin_requires_inputs_key(self, client):
        """
        The request body must have an "inputs" key.
        If it's missing, FastAPI should return 422 (validation error).

        422 = "Unprocessable Entity" — the server understands the request
        but the data doesn't match the expected shape.
        """
        response = await client.post(
            "/run/scraper",
            json={"wrong_key": {}}  # "inputs" is missing
        )
        assert response.status_code == 422, \
            f"Expected 422 validation error, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_run_scraper_with_empty_bill_id_returns_error_in_result(self, client):
        """
        Calling /run/scraper with an empty bill_id should return HTTP 200
        (the endpoint worked) but with an error inside the result dict.

        IMPORTANT distinction:
          HTTP 200 = the endpoint processed the request successfully
          result.error = the BUSINESS LOGIC failed (bad input, download failed, etc.)

        The frontend checks result.error, NOT the HTTP status code for business errors.
        """
        response = await client.post(
            "/run/scraper",
            json={"inputs": {"bill_id": ""}}
        )
        assert response.status_code == 200  # endpoint worked
        data = response.json()
        assert "result" in data
        assert "error" in data["result"]  # but business logic failed

    @pytest.mark.asyncio
    async def test_pipeline_endpoint_exists_and_accepts_request(self, client):
        """
        The /pipeline/full endpoint should exist and accept the right shape.
        We expect it to process and return a result (even if scraping fails
        because we're offline — it shouldn't 404 or 422).
        """
        response = await client.post(
            "/pipeline/full",
            json={"bill_id": "test123", "youtube_url": ""}
        )
        # Should be 200 (processed) even if the result has errors
        assert response.status_code == 200
        data = response.json()
        assert "bill_id" in data
        assert "steps" in data
        assert "errors" in data

    @pytest.mark.asyncio
    async def test_pipeline_requires_bill_id(self, client):
        """
        bill_id is required in the pipeline. Sending without it = 422.
        """
        response = await client.post(
            "/pipeline/full",
            json={"youtube_url": ""}  # missing bill_id
        )
        assert response.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 4: EDGE CASE TESTS
# These test "what if" scenarios — weird inputs, unusual situations.
# Good edge case tests prevent surprises in production.
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_bill_id_with_spaces_is_handled(self, client):
        """
        Users might type " 3076 " with spaces. The app should strip them
        rather than sending " 3076 " to the scraper (which would fail).
        """
        response = await client.post(
            "/run/scraper",
            json={"inputs": {"bill_id": "  3076  "}}
        )
        assert response.status_code == 200
        # We don't assert success — we assert it doesn't CRASH

    @pytest.mark.asyncio
    async def test_pipeline_with_only_bill_id_no_youtube(self, client):
        """
        YouTube URL is optional. The pipeline should still run (and attempt
        to generate a memo) even without a transcript.
        """
        response = await client.post(
            "/pipeline/full",
            json={"bill_id": "test", "youtube_url": ""}
        )
        assert response.status_code == 200
        data = response.json()
        # transcript step should be marked as skipped, not errored
        transcript_step = data.get("steps", {}).get("transcript", {})
        assert transcript_step.get("skipped") == True or "error" not in transcript_step or transcript_step.get("skipped"), \
            "Empty YouTube URL should result in transcript being skipped, not crashed"

    @pytest.mark.asyncio
    async def test_scraper_plugin_has_correct_name(self):
        """
        The plugin name must match the registry key exactly.
        If they don't match, /run/scraper would work but /plugins would show
        the wrong name — a confusing mismatch.
        """
        from plugins.floor_scraper import FloorScraperPlugin
        plugin = FloorScraperPlugin()
        assert plugin.name == "scraper"

    @pytest.mark.asyncio
    async def test_all_plugins_have_non_empty_descriptions(self):
        """
        Every plugin should have a description. The /plugins endpoint
        uses this to tell the frontend what each plugin does.
        """
        from plugins.registry import REGISTRY
        for name, plugin in REGISTRY.items():
            assert plugin.description, \
                f"Plugin '{name}' has an empty description. Add one to make the /plugins endpoint useful."

    @pytest.mark.asyncio
    async def test_memo_plugin_output_type_is_string(self, monkeypatch):
        """
        The memo key in the result must be a string.
        The frontend does string operations on it (e.g. .split(), len()).
        If it's None or a dict, those operations crash.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from plugins.memo_generator import MemoPlugin
        plugin = MemoPlugin()
        result = await plugin.run({"pdf_paths": [], "transcript": "", "bill_id": "test"})

        assert isinstance(result.get("memo"), str), \
            f"memo field must be a string, got {type(result.get('memo'))}"


# ─────────────────────────────────────────────────────────────────────────────
# HOW TO WRITE YOUR OWN TEST CASES
# ─────────────────────────────────────────────────────────────────────────────
#
# Template — copy this and fill in the blanks:
#
# @pytest.mark.asyncio
# async def test_<what you are testing>_<what you expect>(self, client):
#     """
#     ONE LINE describing what question this test answers.
#     """
#     # Arrange — set up your inputs
#     inputs = {"bill_id": "3076"}
#
#     # Act — call the thing you're testing
#     response = await client.post("/run/scraper", json={"inputs": inputs})
#
#     # Assert — check the result
#     assert response.status_code == 200
#     data = response.json()
#     assert "result" in data
#
#
# IDEAS FOR TEST CASES TO WRITE YOURSELF:
#   1. What happens if bill_id contains special characters like "#$%"?
#   2. What if the youtube_url is a valid URL but NOT YouTube (e.g. vimeo)?
#   3. What if pdf_paths contains a path to a file that doesn't exist?
#   4. What if the memo plugin is called with a very short transcript (1 word)?
#   5. What if two different tests both try to create storage/downloads at the same time?
#   6. Does /plugins always return exactly 3 plugins? Or should that number be flexible?
