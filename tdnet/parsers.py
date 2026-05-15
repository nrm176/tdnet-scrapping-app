"""PDF parsing services for completed TDnet disclosure files."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .repository import (
    complete_parse_job,
    count_files_for_parse,
    fail_parse_job,
    get_or_create_parse_job,
    iter_files_for_parse,
    start_parse_job,
)
from .stats_logging import JobStatsLogger

PARSER_NAME = "pymupdf4llm"
NORMALIZER_VERSION = "tdnet-1"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseSummary:
    candidates: int = 0
    parsed: int = 0
    skipped: int = 0
    failed: int = 0
    total_pending: int = 0
    elapsed_seconds: float = 0.0
    files_per_second: float = 0.0
    average_file_seconds: float = 0.0
    median_file_seconds: float = 0.0
    estimated_total_seconds: float | None = None
    estimated_remaining_seconds: float | None = None


@dataclass(frozen=True)
class ParsedPdfArtifacts:
    markdown_path: Path
    pages_path: Path
    metadata_path: Path
    markdown_sha256: str
    page_count: int
    char_count: int


@dataclass(frozen=True)
class _ParseWorkItem:
    storage_path: str


def get_parser_version() -> str:
    """Return the parser identity used to decide whether a file is already parsed."""
    try:
        package_version = metadata.version("pymupdf4llm")
    except metadata.PackageNotFoundError:
        package_version = "unknown"
    try:
        layout_version = metadata.version("pymupdf_layout")
    except metadata.PackageNotFoundError:
        layout_version = None
    layout_part = f"+layout-{layout_version}" if layout_version else ""
    return f"{package_version}{layout_part}+{NORMALIZER_VERSION}"


def _page_number(page: dict[str, Any], fallback: int) -> int:
    metadata_value = page.get("metadata")
    if isinstance(metadata_value, dict):
        number = metadata_value.get("page") or metadata_value.get("page_number")
        if isinstance(number, int):
            return number
    number = page.get("page") or page.get("page_number")
    return number if isinstance(number, int) else fallback


def _page_text(page: dict[str, Any]) -> str:
    for key in ("text", "markdown"):
        value = page.get(key)
        if isinstance(value, str):
            return value
    return ""


def normalize_markdown(value: str) -> str:
    """Apply conservative whitespace cleanup while preserving markdown tables."""
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized: list[str] = []
    blank_seen = False
    for line in lines:
        if not line.strip():
            if not blank_seen:
                normalized.append("")
            blank_seen = True
            continue
        normalized.append(line)
        blank_seen = False
    return "\n".join(normalized).strip() + "\n"


def _coerce_page_chunks(raw_pages: Any) -> list[dict[str, Any]]:
    if isinstance(raw_pages, str):
        return [{"page": 1, "markdown": normalize_markdown(raw_pages), "char_count": len(raw_pages)}]
    if not isinstance(raw_pages, list):
        raise TypeError("PyMuPDF4LLM returned an unsupported result shape")

    pages: list[dict[str, Any]] = []
    for index, raw_page in enumerate(raw_pages, 1):
        if isinstance(raw_page, dict):
            text = normalize_markdown(_page_text(raw_page))
            metadata_value = raw_page.get("metadata")
            page: dict[str, Any] = {
                "page": _page_number(raw_page, index),
                "markdown": text,
                "char_count": len(text),
            }
            if isinstance(metadata_value, dict):
                page["metadata"] = metadata_value
            pages.append(page)
        elif isinstance(raw_page, str):
            text = normalize_markdown(raw_page)
            pages.append({"page": index, "markdown": text, "char_count": len(text)})
        else:
            text = normalize_markdown(str(raw_page))
            pages.append({"page": index, "markdown": text, "char_count": len(text)})
    return pages


def _combined_markdown(pages: list[dict[str, Any]]) -> str:
    sections = []
    for page in pages:
        sections.append(f"<!-- page: {page['page']} -->\n\n{page['markdown'].strip()}")
    return "\n\n".join(sections).strip() + "\n"


def parse_pdf_to_artifacts(
    pdf_path: Path,
    *,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
) -> ParsedPdfArtifacts:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF does not exist: {pdf_path}")

    try:
        import pymupdf4llm
    except ImportError as exc:
        raise RuntimeError("pymupdf4llm is required to parse PDFs") from exc

    version = parser_version or get_parser_version()
    raw_pages = pymupdf4llm.to_markdown(
        str(pdf_path),
        page_chunks=True,
        table_strategy="lines_strict",
        show_progress=False,
    )
    pages = _coerce_page_chunks(raw_pages)
    markdown = _combined_markdown(pages)
    markdown_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

    output_dir = pdf_path.parent / "parsed"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{parser_name}.{version}"
    markdown_path = output_dir / f"{stem}.md"
    pages_path = output_dir / f"{stem}.pages.json"
    metadata_path = output_dir / f"{stem}.meta.json"

    markdown_path.write_text(markdown, encoding="utf-8")
    pages_path.write_text(json.dumps({"pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "parser_name": parser_name,
                "parser_version": version,
                "source_path": str(pdf_path),
                "markdown_path": str(markdown_path),
                "pages_path": str(pages_path),
                "markdown_sha256": markdown_sha256,
                "page_count": len(pages),
                "char_count": len(markdown),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return ParsedPdfArtifacts(
        markdown_path=markdown_path,
        pages_path=pages_path,
        metadata_path=metadata_path,
        markdown_sha256=markdown_sha256,
        page_count=len(pages),
        char_count=len(markdown),
    )


def _parse_pdf_worker(
    storage_path: str,
    parser_name: str,
    parser_version: str,
) -> ParsedPdfArtifacts:
    return parse_pdf_to_artifacts(
        Path(storage_path),
        parser_name=parser_name,
        parser_version=parser_version,
    )


def default_parse_workers() -> int:
    return max(1, os.cpu_count() or 1)


def _process_pool_kwargs() -> dict[str, Any]:
    try:
        return {"mp_context": multiprocessing.get_context("fork")}
    except ValueError:
        return {}


async def parse_pending_files(
    session: AsyncSession,
    *,
    limit: int = 100,
    retry_failed: bool = False,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
    workers: int = 1,
) -> ParseSummary:
    if workers < 1:
        raise ValueError("workers must be at least 1")

    version = parser_version or get_parser_version()
    total_pending = await count_files_for_parse(
        session,
        parser_name=parser_name,
        parser_version=version,
        retry_failed=retry_failed,
    )
    files = await iter_files_for_parse(
        session,
        parser_name=parser_name,
        parser_version=version,
        limit=limit,
        retry_failed=retry_failed,
    )
    parsed = skipped = failed = 0
    work_items: list[tuple[_ParseWorkItem, Any]] = []
    stats = JobStatsLogger(
        job_name="parse",
        logger=logger,
        total_items=total_pending,
        scheduled_items=len(files),
        workers=workers,
    )
    stats.log_start(
        parser_name=parser_name,
        parser_version=version,
        limit=limit,
        retry_failed=retry_failed,
    )

    for file_record in files:
        parse_job = await get_or_create_parse_job(
            session,
            file_record=file_record,
            parser_name=parser_name,
            parser_version=version,
        )
        if parse_job.parse_status == "completed":
            stats.record_skipped(item_id=file_record.id, reason="completed")
            skipped += 1
            continue
        if parse_job.parse_status == "failed" and not retry_failed:
            stats.record_skipped(item_id=file_record.id, reason="failed")
            skipped += 1
            continue

        await start_parse_job(session, parse_job)
        work_items.append((_ParseWorkItem(file_record.storage_path), parse_job))

    if workers == 1 or len(work_items) <= 1:
        for work_item, parse_job in work_items:
            item_start = time.perf_counter()
            try:
                artifacts = parse_pdf_to_artifacts(
                    Path(work_item.storage_path),
                    parser_name=parser_name,
                    parser_version=version,
                )
                await complete_parse_job(
                    session,
                    parse_job,
                    text_path=str(artifacts.markdown_path),
                    text_sha256=artifacts.markdown_sha256,
                )
                parsed += 1
                stats.record_success(
                    item_id=parse_job.file_id,
                    item_seconds=time.perf_counter() - item_start,
                    page_count=artifacts.page_count,
                    char_count=artifacts.char_count,
                    text_path=artifacts.markdown_path,
                )
            except Exception as exc:
                await fail_parse_job(session, parse_job, str(exc))
                failed += 1
                stats.record_failure(
                    item_id=parse_job.file_id,
                    item_seconds=time.perf_counter() - item_start,
                    error=str(exc),
                    storage_path=work_item.storage_path,
                )

        snapshot = stats.log_finish()
        return ParseSummary(
            candidates=len(files),
            parsed=parsed,
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

    loop = asyncio.get_running_loop()
    max_workers = min(workers, len(work_items))
    with ProcessPoolExecutor(max_workers=max_workers, **_process_pool_kwargs()) as executor:
        async def run_work_item(work_item: _ParseWorkItem, parse_job: Any):
            item_start = time.perf_counter()
            try:
                artifacts = await loop.run_in_executor(
                    executor,
                    _parse_pdf_worker,
                    work_item.storage_path,
                    parser_name,
                    version,
                )
                return work_item, parse_job, artifacts, None, time.perf_counter() - item_start
            except Exception as exc:
                return work_item, parse_job, None, exc, time.perf_counter() - item_start

        tasks = [
            asyncio.create_task(run_work_item(work_item, parse_job))
            for work_item, parse_job in work_items
        ]
        for task in asyncio.as_completed(tasks):
            work_item, parse_job, artifacts, error, item_seconds = await task
            if error is not None:
                await fail_parse_job(session, parse_job, str(error))
                failed += 1
                stats.record_failure(
                    item_id=parse_job.file_id,
                    item_seconds=item_seconds,
                    error=str(error),
                    storage_path=work_item.storage_path,
                )
                continue

            await complete_parse_job(
                session,
                parse_job,
                text_path=str(artifacts.markdown_path),
                text_sha256=artifacts.markdown_sha256,
            )
            parsed += 1
            stats.record_success(
                item_id=parse_job.file_id,
                item_seconds=item_seconds,
                page_count=artifacts.page_count,
                char_count=artifacts.char_count,
                text_path=artifacts.markdown_path,
            )

    snapshot = stats.log_finish()
    return ParseSummary(
        candidates=len(files),
        parsed=parsed,
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
