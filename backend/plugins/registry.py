# dict of {name: plugin_instance }

from .floor_scraper import FloorScraperPlugin 
from .transcript import TranscriptPlugin
from .memo_generator import MemoPlugin

REGISTRY = {
    "floor_scraper": FloorScraperPlugin(),
    "transcript": TranscriptPlugin(),
    "memo_generator": MemoPlugin(),
}

