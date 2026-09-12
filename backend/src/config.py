from __future__ import annotations

"""Environment configuration for CRS-01 backend."""
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` relative to the project root, not the process CWD. The
# backend is often launched with `uvicorn src.main:app` from either the
# repo root or backend/, and we want the same .env either way.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# Populate os.environ from the .env file so every module that reads raw
# env vars (e.g. ingestion/research/providers.py, llm_extractor.py) sees
# TAVILY_API_KEY / PERPLEXITY_API_KEY / OPENAI_API_KEY. Real process env
# vars win (load_dotenv does not override by default).
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Wayback Machine — opt-in: after each research merge, check archive.org
    # for captures of new sources and persist them (≤10 URLs, ~4s each).
    # Default off — the merge path stays network-free. Read by
    # src/ingestion/archive.py.
    wayback_autolookup: bool = Field(
        default=False, validation_alias="CRS_WAYBACK_AUTOLOOKUP"
    )

    # FastAPI
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001", "http://localhost:8000"]
    api_title: str = "CRS-01 API"
    api_version: str = "0.1.0"


settings = Settings()
