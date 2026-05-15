"""Runtime configuration for the TDnet service."""
from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_DATABASE_URL = "postgresql+asyncpg://tdnet:tdnet@localhost:55432/tdnet"
DEFAULT_DOWNLOAD_ROOT = "/Volumes/yakushimachi/Downloads"


@dataclass(frozen=True)
class Settings:
    """Environment-backed application settings."""

    database_url: str = DEFAULT_DATABASE_URL
    download_root: str = DEFAULT_DOWNLOAD_ROOT
    api_title: str = "TDnet API"
    api_version: str = "0.1.0"


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        download_root=os.getenv("TDNET_DOWNLOAD_ROOT", DEFAULT_DOWNLOAD_ROOT),
        api_title=os.getenv("TDNET_API_TITLE", "TDnet API"),
        api_version=os.getenv("TDNET_API_VERSION", "0.1.0"),
    )
