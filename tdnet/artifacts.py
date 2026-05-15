"""Download and artifact tracking for TDnet disclosure files."""
from __future__ import annotations

import hashlib
import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .constants import HEADERS
from .database import SessionLocal
from .orm import DisclosureRecord
from .repository import (
    complete_disclosure_file_by_id,
    fail_disclosure_file_by_id,
    get_or_create_disclosure_file,
    iter_disclosures_for_download,
)

DEFAULT_BUCKET = "tdnet"
FORECAST_CORRECTION_BUCKET = "tdnet-forecast-correction"
FORECAST_CORRECTION_KEYWORDS = ("業績予想", "予想値")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadSummary:
    candidates: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0
    total_bytes: int = 0
    average_file_seconds: float = 0.0
    median_file_seconds: float = 0.0


@dataclass(frozen=True)
class DownloadTask:
    file_id: int
    source_url: str
    storage_path: Path


@dataclass(frozen=True)
class DownloadResult:
    status: str
    file_id: int
    source_url: str
    elapsed_seconds: float
    bytes_downloaded: int = 0


def is_forecast_correction(title: str) -> bool:
    return any(keyword in title for keyword in FORECAST_CORRECTION_KEYWORDS)


def storage_bucket_for_disclosure(disclosure: DisclosureRecord) -> str:
    if is_forecast_correction(disclosure.title):
        return FORECAST_CORRECTION_BUCKET
    return DEFAULT_BUCKET


def safe_filename_part(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "-", value.strip())
    return normalized.strip("-") or "unknown"


def source_file_id(source_url: str) -> str:
    stem = Path(urlparse(source_url).path).stem
    return safe_filename_part(stem)


def extension_for_file(file_type: str, source_url: str) -> str:
    suffix = Path(urlparse(source_url).path).suffix.lower()
    if suffix:
        return suffix
    return ".zip" if file_type == "xbrl" else ".pdf"


def build_storage_path(
    root: Path,
    disclosure: DisclosureRecord,
    *,
    file_type: str,
    source_url: str,
) -> tuple[str, Path]:
    bucket = storage_bucket_for_disclosure(disclosure)
    folder = source_file_id(disclosure.pdf_url) or disclosure.id
    file_id = source_file_id(source_url)
    extension = extension_for_file(file_type, source_url)
    filename = f"{file_id}{extension}"
    return bucket, root / bucket / folder / filename


async def _write_bytes(destination: Path, data: bytes) -> None:
    def write() -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_destination = destination.with_suffix(destination.suffix + ".tmp")
        with temp_destination.open("wb") as f:
            f.write(data)
        temp_destination.replace(destination)

    await asyncio.to_thread(write)


async def _download_file(
    client: httpx.AsyncClient,
    source_url: str,
    destination: Path,
) -> tuple[int, str, str | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = await client.get(source_url)
    response.raise_for_status()
    content = response.content
    await _write_bytes(destination, content)
    return len(content), hashlib.sha256(content).hexdigest(), response.headers.get("content-type")


async def _run_download_task(
    task: DownloadTask,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> DownloadResult:
    async with semaphore:
        start = time.perf_counter()
        try:
            size, sha256, content_type = await _download_file(
                client,
                task.source_url,
                task.storage_path,
            )
            elapsed = time.perf_counter() - start
            async with SessionLocal() as session:
                await complete_disclosure_file_by_id(
                    session,
                    task.file_id,
                    file_size_bytes=size,
                    sha256=sha256,
                    content_type=content_type,
                )
            logger.info(
                "Downloaded file_id=%s bytes=%s seconds=%.3f path=%s",
                task.file_id,
                size,
                elapsed,
                task.storage_path,
            )
            return DownloadResult(
                status="downloaded",
                file_id=task.file_id,
                source_url=task.source_url,
                elapsed_seconds=elapsed,
                bytes_downloaded=size,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - start
            logger.warning(
                "Download failed file_id=%s seconds=%.3f url=%s error=%s",
                task.file_id,
                elapsed,
                task.source_url,
                exc,
            )
            async with SessionLocal() as session:
                await fail_disclosure_file_by_id(session, task.file_id, str(exc))
            return DownloadResult(
                status="failed",
                file_id=task.file_id,
                source_url=task.source_url,
                elapsed_seconds=elapsed,
            )


async def download_pending_files(
    session: AsyncSession,
    *,
    root: Path | None = None,
    limit: int = 100,
    retry_failed: bool = False,
    concurrency: int = 8,
) -> DownloadSummary:
    job_start = time.perf_counter()
    download_root = root or Path(get_settings().download_root)
    disclosures = await iter_disclosures_for_download(
        session,
        limit=limit,
        retry_failed=retry_failed,
    )
    logger.info(
        "Prepared disclosure download candidates=%s limit=%s concurrency=%s retry_failed=%s",
        len(disclosures),
        limit,
        concurrency,
        retry_failed,
    )
    tasks: list[DownloadTask] = []
    skipped = 0

    for disclosure in disclosures:
        targets: list[tuple[str, str]] = [("pdf", disclosure.pdf_url)]
        if disclosure.xbrl_available and disclosure.xbrl_url:
            targets.append(("xbrl", disclosure.xbrl_url))

        for file_type, source_url in targets:
            bucket, storage_path = build_storage_path(
                download_root,
                disclosure,
                file_type=file_type,
                source_url=source_url,
            )
            file_record = await get_or_create_disclosure_file(
                session,
                disclosure=disclosure,
                file_type=file_type,
                source_url=source_url,
                source_file_id=source_file_id(source_url),
                storage_bucket=bucket,
                storage_path=str(storage_path),
            )

            if (
                file_record.download_status == "completed"
                and storage_path.exists()
                and not retry_failed
            ):
                logger.debug("Skipping completed file_id=%s path=%s", file_record.id, storage_path)
                skipped += 1
                continue
            if file_record.download_status == "failed" and not retry_failed:
                logger.debug("Skipping failed file_id=%s path=%s", file_record.id, storage_path)
                skipped += 1
                continue

            tasks.append(
                DownloadTask(
                    file_id=file_record.id,
                    source_url=source_url,
                    storage_path=storage_path,
                )
            )

    timeout = httpx.Timeout(60.0, connect=20.0)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
        results: list[DownloadResult] = await asyncio.gather(
            *(_run_download_task(task, client, semaphore) for task in tasks)
        )

    elapsed = time.perf_counter() - job_start
    downloaded_results = [result for result in results if result.status == "downloaded"]
    failed_results = [result for result in results if result.status == "failed"]
    durations = [result.elapsed_seconds for result in results]
    total_bytes = sum(result.bytes_downloaded for result in downloaded_results)
    throughput = total_bytes / elapsed if elapsed > 0 else 0.0
    logger.info(
        "Download statistics scheduled=%s downloaded=%s failed=%s skipped=%s elapsed_seconds=%.3f total_bytes=%s throughput_bytes_per_second=%.2f average_file_seconds=%.3f median_file_seconds=%.3f",
        len(tasks),
        len(downloaded_results),
        len(failed_results),
        skipped,
        elapsed,
        total_bytes,
        throughput,
        mean(durations) if durations else 0.0,
        median(durations) if durations else 0.0,
    )
    return DownloadSummary(
        candidates=len(disclosures),
        downloaded=len(downloaded_results),
        skipped=skipped,
        failed=len(failed_results),
        elapsed_seconds=elapsed,
        total_bytes=total_bytes,
        average_file_seconds=mean(durations) if durations else 0.0,
        median_file_seconds=median(durations) if durations else 0.0,
    )
