"""Async SQLAlchemy setup."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings
from .orm import Base


def create_engine(database_url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        database_url or get_settings().database_url,
        pool_pre_ping=True,
    )


engine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db(target_engine: AsyncEngine | None = None) -> None:
    """Create database tables for local/dev use.

    A later Alembic migration layer can own schema evolution; this keeps v1
    runnable with only Docker Postgres and the package installed.
    """
    bind = target_engine or engine
    async with bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
