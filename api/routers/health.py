"""Liveness and readiness."""
from __future__ import annotations

import time

from fastapi import APIRouter

from .. import db
from ..models import DatasetInfo, HealthResponse
from ..settings import settings
from ..store import store

router = APIRouter(tags=["health"])
_STARTED = time.time()


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health() -> HealthResponse:
    """Report what is loaded and whether the stream is attached.

    Returns 200 even when the stream is down: the batch endpoints still work,
    and an orchestrator restarting the container would not fix a stream outage.
    """
    datasets = [
        DatasetInfo(name=name, rows=len(frame), columns=list(frame.columns)[:12])
        for name, frame in sorted(store.frames.items())
    ]
    return HealthResponse(
        status="ok" if store.frames else "degraded",
        version=settings.version,
        extracts_loaded=len(store.frames),
        datasets=datasets,
        stream_connected=db.connected(),
        uptime_seconds=round(time.time() - _STARTED, 1),
    )
