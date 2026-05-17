"""Postgres search support for the web app."""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def ensure_postgres_search_support(engine: AsyncEngine) -> None:
    """Create optional Postgres trigram indexes used by ILIKE search."""
    if engine.dialect.name != "postgresql":
        return

    statements = [
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        (
            "CREATE INDEX IF NOT EXISTS ix_document_parse_texts_content_trgm "
            "ON document_parse_texts USING gin (content_text gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_tdnet_disclosures_title_trgm "
            "ON tdnet_disclosures USING gin (title gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_tdnet_disclosures_name_trgm "
            "ON tdnet_disclosures USING gin (name gin_trgm_ops)"
        ),
    ]
    async with engine.begin() as conn:
        for statement in statements:
            try:
                await conn.execute(text(statement))
            except Exception:
                logger.exception("Failed to prepare Postgres search support: %s", statement)
                raise
