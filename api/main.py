"""FastAPI application.

Extracts are read once in the lifespan handler rather than per request. The
stream connection is attempted at startup but never required: if it fails the
batch endpoints keep serving and /health reports the degradation.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from . import db
from .routers import admin, customers, dashboard, health, kpi, live, products
from .settings import settings
from .store import store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.folder = settings.extract_dir
    loaded = store.load()
    log.info("extracts loaded: %d from %s", loaded, store.folder)

    if settings.stream_enabled:
        db.init(settings.stream_dsn)
    else:
        log.info("streaming disabled by configuration")

    yield

    db.dispose()
    log.info("shutdown complete")


app = FastAPI(
    title=settings.title,
    version=settings.version,
    root_path=settings.root_path,
    lifespan=lifespan,
    description=(
        "Analytics over the Online Retail II warehouse.\n\n"
        "**/kpi**, **/products** and **/customers** serve the reconciled batch "
        "extracts. **/live** serves the Spark Structured Streaming aggregates, "
        "which are approximate and continuously moving. The two are kept apart "
        "deliberately - they answer different questions."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

V1 = "/api/v1"
app.include_router(health.router, prefix=V1)
app.include_router(kpi.router, prefix=V1)
app.include_router(products.router, prefix=V1)
app.include_router(customers.router, prefix=V1)
app.include_router(live.router, prefix=V1)
app.include_router(admin.router, prefix=V1)
app.include_router(dashboard.router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")
