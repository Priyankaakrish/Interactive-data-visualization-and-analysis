"""Configuration, paths and theme.

All tuning lives in config.yaml. The database DSN additionally honours the
DATABASE_URL environment variable, so the same code runs against a local
PostgreSQL, a container, or a managed instance in CI without file edits.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True)
class Theme:
    """One source of visual truth for Plotly, Excel and Power BI."""

    name: str = "Indigo Retail"
    primary: str = "#243B6B"
    secondary: str = "#3E7CB1"
    accent: str = "#E0A458"
    positive: str = "#2E8B57"
    negative: str = "#B23A48"
    neutral: str = "#7C8A9B"
    grid: str = "#E4E9F0"
    background: str = "#FFFFFF"
    font: str = "Segoe UI"

    @property
    def categorical(self) -> list[str]:
        return [self.primary, self.secondary, self.accent, self.positive,
                self.negative, self.neutral, "#6A4C93", "#1B7B8C"]


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)

    # -- source ----------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        p = PROJECT_ROOT / self.raw.get("source", {}).get("raw_dir", "data/raw")
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def excel_name(self) -> str:
        return self.raw.get("source", {}).get("excel_name", "online_retail_II.xlsx")

    @property
    def allow_sample_fallback(self) -> bool:
        return self.raw.get("source", {}).get("allow_sample_fallback", True)

    # -- database --------------------------------------------------------
    @property
    def db(self) -> dict[str, Any]:
        return self.raw.get("database", {})

    @property
    def dsn(self) -> str:
        return os.environ.get("DATABASE_URL") or self.db.get("dsn", "")

    def schema(self, layer: str) -> str:
        return self.raw.get("schemas", {}).get(layer, layer)

    # -- sections --------------------------------------------------------
    @property
    def cleaning(self) -> dict[str, Any]:
        return self.raw.get("cleaning", {})

    @property
    def validation(self) -> dict[str, Any]:
        return self.raw.get("validation", {})

    @property
    def monitoring(self) -> dict[str, Any]:
        return self.raw.get("monitoring", {})

    @property
    def kpi(self) -> dict[str, Any]:
        return self.raw.get("kpi", {})

    @property
    def currency(self) -> str:
        return self.kpi.get("currency_symbol", "£")

    @property
    def theme(self) -> Theme:
        return Theme(**self.raw.get("theme", {}))

    def path(self, key: str) -> Path:
        rel = self.raw.get("paths", {}).get(key, key)
        p = PROJECT_ROOT / rel
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return Config(raw={})
    with cfg_path.open("r", encoding="utf-8") as fh:
        return Config(raw=yaml.safe_load(fh) or {})
