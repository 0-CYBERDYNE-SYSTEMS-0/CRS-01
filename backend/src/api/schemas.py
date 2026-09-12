from __future__ import annotations

"""Pydantic request/response schemas for CRS-01 API.

Routes define their own request/response models next to their handlers;
only the shared health schema lives here.
"""
from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
