# Base plugin abstract class (you write this FIRST)
from abc import ABC, abstractmethod

class BasePlugin(ABC):
    name: str = ""
    description: str = ""

    @abstractmethod
    async def run(self, inputs: dict) -> dict:
        """Always takes a dict, always returns a dict"""
        pass
    