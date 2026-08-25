"""Runtime configuration, resolved from environment variables with defaults."""
from __future__ import annotations

import os
from pathlib import Path


class Settings:
    """Container for every value the service reads from its environment."""

    def __init__(self) -> None:
        self.title = "Online Retail II Analytics API"
        self.version = "1.0.0"
        self.root_path = os.getenv("API_ROOT_PATH", "")

        # Published CSV extracts - the same folder Power BI consumes.
        self.extract_dir = Path(os.getenv("EXTRACT_DIR", "data/processed")).resolve()

        # Streaming warehouse. Optional: live endpoints degrade rather than fail.
        self.stream_dsn = os.getenv(
            "STREAM_DSN", "postgresql+psycopg://retail:retail@postgres:5432/retail")
        self.stream_enabled = os.getenv("STREAM_ENABLED", "1") not in ("0", "false", "False")

        self.cors_origins = [o for o in os.getenv("CORS_ORIGINS", "*").split(",") if o]


settings = Settings()
