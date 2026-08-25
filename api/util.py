"""Small helpers shared by the routers."""
from __future__ import annotations

import pandas as pd


def pick(frame: pd.DataFrame, *candidates: str) -> str | None:
    """Return the first column present, so minor extract renames do not break the API."""
    lowered = {c.lower(): c for c in frame.columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def num(frame: pd.DataFrame, *candidates: str, default: float = 0.0) -> float:
    col = pick(frame, *candidates)
    if col is None or frame.empty:
        return default
    value = pd.to_numeric(frame[col], errors="coerce").dropna()
    return float(value.iloc[0]) if len(value) else default


def records(frame: pd.DataFrame, mapping: dict, limit: int = 0) -> list[dict]:
    """Project a frame onto the response model's field names, tolerating absences."""
    out = frame.head(limit) if limit else frame
    rows = []
    for _, r in out.iterrows():
        row = {}
        for field, candidates in mapping.items():
            col = pick(frame, *candidates)
            val = r[col] if col is not None else None
            if pd.isna(val):
                val = None
            row[field] = val
        rows.append(row)
    return rows


def kv_lookup(frame: pd.DataFrame, key_col: str, val_col: str, key: str,
              default: float = 0.0) -> float:
    """Read a value from a metric/value shaped extract such as executive_summary."""
    hit = frame[frame[key_col].astype(str).str.lower() == key.lower()]
    if hit.empty:
        return default
    value = pd.to_numeric(hit[val_col], errors="coerce").dropna()
    return float(value.iloc[0]) if len(value) else default
