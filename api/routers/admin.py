"""Operational endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from ..store import store

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reload", summary="Re-read the extract folder")
def reload() -> dict:
    """Pick up a new pipeline run without restarting the container.

    The reload is atomic from a reader's point of view because the store swaps
    the whole dictionary rather than mutating frames in place.
    """
    count = store.load()
    return {"reloaded": count, "datasets": sorted(store.frames)}


@router.get("/datasets", summary="What is loaded")
def datasets() -> dict:
    return {name: {"rows": len(f), "columns": list(f.columns)}
            for name, f in sorted(store.frames.items())}
