"""Connection to the streaming warehouse.

Deliberately optional. If the stream database is down the batch endpoints must
keep serving, so every helper here returns None or an empty frame rather than
raising, and the health endpoint reports the degradation honestly.
"""
from __future__ import annotations

import logging

import pandas as pd
import sqlalchemy as sa

log = logging.getLogger("api.db")

_engine: sa.Engine | None = None


def init(dsn: str) -> bool:
    """Create the engine and verify it. Returns True when the stream is reachable."""
    global _engine
    try:
        engine = sa.create_engine(dsn, pool_pre_ping=True, pool_size=5, max_overflow=5)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        _engine = engine
        log.info("stream database connected")
        return True
    except Exception as exc:
        log.warning("stream database unavailable: %s", exc)
        _engine = None
        return False


def connected() -> bool:
    return _engine is not None


def dispose() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def query(sql: str, **params) -> pd.DataFrame:
    if _engine is None:
        return pd.DataFrame()
    try:
        with _engine.connect() as conn:
            return pd.read_sql(sa.text(sql), conn, params=params)
    except Exception as exc:
        log.error("stream query failed: %s", exc)
        return pd.DataFrame()
