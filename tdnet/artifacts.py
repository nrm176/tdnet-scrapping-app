"""Download and artifact tracking for TDnet disclosure files."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .constants import HEADERS
from .orm import DisclosureRecord
from .repository import (
    complete_disclosure_file,
    fail_disclosure_file,
    get_or_create_disclosure_file,
    iter_disclosures_for_download,
)

DEFAULT_BUCKET = "tdnet"
FORECAST_CORRECTION_BUCKET = "tdnet-forecast-correction"
FORECAST_CORRECTION_KEYWORDS = ("業績予想", "予想値")


@dataclass(frozen=True)
class DownloadSummary:
    candidates: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0


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


def _download_file(source_url: str, destination: Path) -> tuple[int, str, str | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0

    with requests.get(source_url, headers=HEADERS, stream=True, timeout=60) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type")
        temp_destination = destination.with_suffix(destination.suffix + ".tmp")
        with temp_destination.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
        temp_destination.replace(destination)

    return size, hasher.hexdigest(), content_type


async def download_pending_files(
    session: AsyncSession,
    *,
    root: Path | None = None,
    limit: int = 100,
    retry_failed: bool = False,
) -> DownloadSummary:
    download_root = root or Path(get_settings().download_root)
    disclosures = await iter_disclosures_for_download(
        session,
        limit=limit,
        retry_failed=retry_failed,
    )
    summary = DownloadSummary(candidates=len(disclosures))
    downloaded = skipped = failed = 0

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
                skipped += 1
                continue
            if file_record.download_status == "failed" and not retry_failed:
                skipped += 1
                continue

            try:
                size, sha256, content_type = _download_file(source_url, storage_path)
                await complete_disclosure_file(
                    session,
                    file_record,
                    file_size_bytes=size,
                    sha256=sha256,
                    content_type=content_type,
                )
                downloaded += 1
            except Exception as exc:
                await fail_disclosure_file(session, file_record, str(exc))
                failed += 1

    return DownloadSummary(
        candidates=summary.candidates,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
    )
