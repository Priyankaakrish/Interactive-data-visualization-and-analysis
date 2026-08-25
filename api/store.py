"""In-memory store for the published extracts.

The CSVs are read once at application startup and held as DataFrames. A request
never touches the filesystem, which keeps p95 latency flat and means a refresh
midway through a request cannot produce a half-updated response.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger("api.store")


class ExtractStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.frames: dict[str, pd.DataFrame] = {}
        self.loaded_at: float | None = None

    def load(self) -> int:
        """Read every CSV in the extract folder. Returns the number loaded."""
        self.frames.clear()
        if not self.folder.exists():
            log.warning("extract folder does not exist: %s", self.folder)
            self.loaded_at = time.time()
            return 0

        for path in sorted(self.folder.glob("*.csv")):
            try:
                self.frames[path.stem] = pd.read_csv(path)
            except Exception as exc:  # a malformed extract must not kill the service
                log.error("could not read %s: %s", path.name, exc)

        self.loaded_at = time.time()
        log.info("loaded %d extracts from %s", len(self.frames), self.folder)
        return len(self.frames)

    def get(self, name: str) -> pd.DataFrame:
        if name not in self.frames:
            raise KeyError(name)
        return self.frames[name]

    def first(self, *names: str) -> pd.DataFrame:
        """Return the first extract that exists, so column naming can vary."""
        for name in names:
            if name in self.frames:
                return self.frames[name]
        raise KeyError(" / ".join(names))

    def has(self, *names: str) -> bool:
        return any(n in self.frames for n in names)


store = ExtractStore(Path("data/processed"))
