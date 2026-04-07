"""
Plugin Registry
================
This is the ONLY file you edit when adding a new plugin.
Everything else — main.py, the frontend, the tests — stays the same.

To add a new plugin:
  1. Create backend/plugins/my_new_plugin.py  (copy the pattern from any existing plugin)
  2. Import it below
  3. Add one line to REGISTRY

That's it. The FastAPI backend automatically exposes it at /run/my_new_plugin
"""

from .floor_scraper import FloorScraperPlugin  # Senate Floor System (requires Senate network)
from .mga_scraper import MGAScraperPlugin      # Public fallback (mgaleg.maryland.gov)

from .transcript import TranscriptPlugin
from .memo_generator import MemoPlugin

REGISTRY = {
    "scraper":    FloorScraperPlugin(),        # ← Floor System (login required on Senate network)
    # "scraper":  MGAScraperPlugin(),          # ← swap in for offline/dev testing
    "transcript": TranscriptPlugin(),
    "memo":       MemoPlugin(),
    # "stance":  StanceDetectorPlugin(),   ← uncomment when ready
}