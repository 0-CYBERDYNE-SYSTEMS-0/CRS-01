from __future__ import annotations
"""FastAPI entry point for CRS-01 Cannabis Research Sentinel."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .api.routes import (
    graph,
    archive,
    neighborhood,
    research,
    evidence,
    curation,
)
from .api.schemas import HealthResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logging — nothing more. The SQLite KB needs no
    connection held open; every accessor opens its own short-lived session."""
    logger.info("Starting CRS-01 API")
    yield
    logger.info("Shutting down CRS-01 API")


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(graph.router, prefix="/api/v1")
app.include_router(neighborhood.router, prefix="/api/v1")
app.include_router(evidence.router, prefix="/api/v1")
app.include_router(curation.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(archive.router, prefix="/api/v1")


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint."""
    from datetime import datetime

    return HealthResponse(
        status="ok",
        version=settings.api_version,
        timestamp=datetime.utcnow(),
    )


@app.get("/", tags=["root"])
async def root():
    """Root endpoint."""
    return {
        "name": "CRS-01 Cannabis Research Sentinel",
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health",
    }
