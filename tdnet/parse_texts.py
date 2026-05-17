"""Persistence helpers for searchable parsed document text."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .orm import DocumentParseJobRecord, DocumentParseTextRecord
from .repository import upsert_parse_text
from .stats_logging import JobStatsLogger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseTextPayload:
    content_text: str
    pages_json: dict | None
    page_count: int
    char_count: int
    content_sha256: str


@dataclass(frozen=True)
class ParseTextBackfillSummary:
    candidates: int = 0
    persisted: int = 0
    skipped: int = 0
    failed: int = 0
    total_pending: int = 0
    elapsed_seconds: float = 0.0
    files_per_second: float = 0.0
    average_file_seconds: float = 0.0
    median_file_seconds: float = 0.0
    estimated_total_seconds: float | None = None
    estimated_remaining_seconds: float | None = None


def pages_path_for_markdown(markdown_path: Path) -> Path:
    return markdown_path.with_name(markdown_path.name.replace(".md", ".pages.json"))


def sanitize_postgres_text(value: str) -> str:
    """Remove characters PostgreSQL text/JSONB cannot store."""
    return value.replace("\x00", "")


def sanitize_postgres_json(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_postgres_text(value)
    if isinstance(value, list):
        return [sanitize_postgres_json(item) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_postgres_text(str(key)): sanitize_postgres_json(item)
            for key, item in value.items()
        }
    return value


def load_parse_text_payload(markdown_path: Path, pages_path: Path | None = None) -> ParseTextPayload:
    if not markdown_path.exists():
        raise FileNotFoundError(f"Parsed markdown does not exist: {markdown_path}")

    content_text = sanitize_postgres_text(markdown_path.read_text(encoding="utf-8"))
    resolved_pages_path = pages_path or pages_path_for_markdown(markdown_path)
    pages_json: dict[str, Any] | None = None
    page_count = 0
    if resolved_pages_path.exists():
        raw_pages = json.loads(resolved_pages_path.read_text(encoding="utf-8"))
        if isinstance(raw_pages, dict):
            pages_json = sanitize_postgres_json(raw_pages)
            raw_page_list = raw_pages.get("pages")
            if isinstance(raw_page_list, list):
                page_count = len(raw_page_list)

    return ParseTextPayload(
        content_text=content_text,
        pages_json=pages_json,
        page_count=page_count,
        char_count=len(content_text),
        content_sha256=hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
    )


async def persist_parse_text_artifacts(
    session: AsyncSession,
    *,
    parse_job: DocumentParseJobRecord,
    markdown_path: Path,
    pages_path: Path | None = None,
) -> DocumentParseTextRecord:
    payload = load_parse_text_payload(markdown_path, pages_path)
    return await upsert_parse_text(
        session,
        parse_job=parse_job,
        content_text=payload.content_text,
        pages_json=payload.pages_json,
        page_count=payload.page_count,
        char_count=payload.char_count,
        content_sha256=payload.content_sha256,
    )


async def count_parse_text_backfill_candidates(
    session: AsyncSession,
    *,
    parser_name: str | None = None,
    parser_version: str | None = None,
) -> int:
    parse_text_exists = (
        select(DocumentParseTextRecord.id)
        .where(DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
        .exists()
    )
    stmt = (
        select(func.count())
        .select_from(DocumentParseJobRecord)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .where(DocumentParseJobRecord.text_path.is_not(None))
        .where(~parse_text_exists)
    )
    if parser_name is not None:
        stmt = stmt.where(DocumentParseJobRecord.parser_name == parser_name)
    if parser_version is not None:
        stmt = stmt.where(DocumentParseJobRecord.parser_version == parser_version)
    return int(await session.scalar(stmt) or 0)


async def iter_parse_text_backfill_jobs(
    session: AsyncSession,
    *,
    parser_name: str | None = None,
    parser_version: str | None = None,
    limit: int = 100,
) -> list[DocumentParseJobRecord]:
    parse_text_exists = (
        select(DocumentParseTextRecord.id)
        .where(DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
        .exists()
    )
    stmt = (
        select(DocumentParseJobRecord)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .where(DocumentParseJobRecord.text_path.is_not(None))
        .where(~parse_text_exists)
        .order_by(DocumentParseJobRecord.parsed_at.desc(), DocumentParseJobRecord.id.desc())
        .limit(limit)
    )
    if parser_name is not None:
        stmt = stmt.where(DocumentParseJobRecord.parser_name == parser_name)
    if parser_version is not None:
        stmt = stmt.where(DocumentParseJobRecord.parser_version == parser_version)
    return list((await session.scalars(stmt)).all())


async def backfill_parse_texts(
    session: AsyncSession,
    *,
    parser_name: str | None = None,
    parser_version: str | None = None,
    limit: int = 100,
) -> ParseTextBackfillSummary:
    total_pending = await count_parse_text_backfill_candidates(
        session,
        parser_name=parser_name,
        parser_version=parser_version,
    )
    parse_jobs = await iter_parse_text_backfill_jobs(
        session,
        parser_name=parser_name,
        parser_version=parser_version,
        limit=limit,
    )
    persisted = skipped = failed = 0
    stats = JobStatsLogger(
        job_name="parse_text_backfill",
        logger=logger,
        total_items=total_pending,
        scheduled_items=len(parse_jobs),
        workers=1,
    )
    stats.log_start(
        parser_name=parser_name,
        parser_version=parser_version,
        limit=limit,
    )

    for parse_job in parse_jobs:
        item_start = time.perf_counter()
        if not parse_job.text_path:
            skipped += 1
            stats.record_skipped(item_id=parse_job.id, reason="missing_text_path")
            continue
        try:
            await persist_parse_text_artifacts(
                session,
                parse_job=parse_job,
                markdown_path=Path(parse_job.text_path),
            )
            persisted += 1
            stats.record_success(
                item_id=parse_job.id,
                item_seconds=time.perf_counter() - item_start,
                parser_name=parse_job.parser_name,
                parser_version=parse_job.parser_version,
                text_path=parse_job.text_path,
            )
        except Exception as exc:
            await session.rollback()
            failed += 1
            stats.record_failure(
                item_id=parse_job.id,
                item_seconds=time.perf_counter() - item_start,
                error=str(exc),
                parser_name=parse_job.parser_name,
                parser_version=parse_job.parser_version,
                text_path=parse_job.text_path,
            )

    snapshot = stats.log_finish()
    return ParseTextBackfillSummary(
        candidates=len(parse_jobs),
        persisted=persisted,
        skipped=skipped,
        failed=failed,
        total_pending=total_pending,
        elapsed_seconds=snapshot.elapsed_seconds,
        files_per_second=snapshot.items_per_second,
        average_file_seconds=snapshot.average_item_seconds,
        median_file_seconds=snapshot.median_item_seconds,
        estimated_total_seconds=snapshot.estimated_total_seconds,
        estimated_remaining_seconds=snapshot.estimated_remaining_seconds,
    )
