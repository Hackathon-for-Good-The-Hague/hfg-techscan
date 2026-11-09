"""Base adapter interface. Each adapter provides submissions from a platform."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class AdapterSubmission:
    """Minimal submission data an adapter must provide."""
    name: str
    repo_url: str = ""
    description: str = ""
    punchline: str = ""
    demo_url: str = ""
    video_url: str = ""
    challenge: str = ""
    status: str = ""
    team_size: int = 0

class BaseAdapter(ABC):
    """Interface for hackathon platform adapters."""

    @abstractmethod
    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        """Pull submissions from the platform. Returns list of AdapterSubmission."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable platform name."""
        ...
