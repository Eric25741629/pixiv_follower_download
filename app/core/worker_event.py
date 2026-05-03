from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerEvent:
    """Immutable event emitted by worker threads via queue.Queue.

    type values:
      "output"     – data: str  (HTML-colored log line)
      "progress"   – data: tuple[int, int]  (current, total)
      "countdown"  – data: int  (remaining seconds)
      "finished"   – data: str  (completion message)
      "next"       – data: int  (next step number; -1 = stop)
      "timechanged"– data: str  (ISO datetime string, download_thread only)
    """
    type: str
    data: Any
