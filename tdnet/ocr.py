"""OCR services for TDnet PDFs that have sparse embedded text."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .orm import DisclosureFileRecord, DisclosureRecord, DocumentParseJobRecord
from .parse_texts import persist_parse_text_artifacts
from .parsers import NORMALIZER_VERSION, PARSER_NAME, get_parser_version, normalize_markdown
from .repository import (
    complete_parse_job,
    fail_parse_job,
    get_or_create_parse_job,
    start_parse_job,
)
from .review import ParsedPage, score_pages
from .stats_logging import JobStatsLogger

logger = logging.getLogger(__name__)

APPLE_VISION_OCR_NAME = "apple-vision-ocr"
APPLE_VISION_HELPER_VERSION = "vision-swift-1"
OcrStrategy = Literal["low-text", "forecast-correction", "all"]


@dataclass(frozen=True)
class OcrArtifacts:
    markdown_path: Path
    pages_path: Path
    metadata_path: Path
    markdown_sha256: str
    page_count: int
    char_count: int


@dataclass(frozen=True)
class OcrCandidate:
    file_record: DisclosureFileRecord
    source_parse_job: DocumentParseJobRecord
    disclosure: DisclosureRecord
    pages: tuple[ParsedPage, ...]
    suspicion_score: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class OcrSummary:
    candidates: int = 0
    ocr_completed: int = 0
    skipped: int = 0
    failed: int = 0
    total_pending: int = 0
    elapsed_seconds: float = 0.0
    files_per_second: float = 0.0
    average_file_seconds: float = 0.0
    median_file_seconds: float = 0.0
    estimated_total_seconds: float | None = None
    estimated_remaining_seconds: float | None = None


class OcrProvider(Protocol):
    name: str
    version: str

    def is_available(self) -> bool:
        """Return whether this provider can run in the current environment."""

    def ocr_pdf(self, pdf_path: Path) -> OcrArtifacts:
        """OCR a PDF and write parse artifacts."""


def get_apple_vision_parser_version() -> str:
    """Return a stable parser identity for the bundled Apple Vision helper."""
    macos_version = platform.mac_ver()[0] or "unknown"
    macos_part = re.sub(r"[^0-9A-Za-z.]+", "-", macos_version).strip("-") or "unknown"
    return f"{APPLE_VISION_HELPER_VERSION}+macos-{macos_part}+{NORMALIZER_VERSION}"


def _helper_path() -> Path:
    return Path(__file__).with_name("vision_ocr.swift")


def _pages_path_for_markdown(markdown_path: Path) -> Path:
    return markdown_path.with_name(markdown_path.name.replace(".md", ".pages.json"))


def _load_source_pages(text_path: Path) -> list[ParsedPage]:
    pages_path = _pages_path_for_markdown(text_path)
    if not pages_path.exists():
        return []

    raw = json.loads(pages_path.read_text(encoding="utf-8"))
    pages: list[ParsedPage] = []
    for index, page in enumerate(raw.get("pages", []), 1):
        if not isinstance(page, dict):
            continue
        markdown = str(page.get("markdown") or "")
        page_number = page.get("page")
        pages.append(
            ParsedPage(
                page=page_number if isinstance(page_number, int) else index,
                markdown=markdown,
                char_count=int(page.get("char_count") or len(markdown)),
            )
        )
    return pages


def needs_ocr(
    pages: list[ParsedPage] | tuple[ParsedPage, ...],
    *,
    file_size_bytes: int | None = None,
    minimum_score: int = 40,
) -> tuple[bool, int, tuple[str, ...]]:
    """Return whether a parsed PDF is sparse enough to justify OCR."""
    score, warnings = score_pages(list(pages), file_size_bytes=file_size_bytes)
    total_chars = sum(page.char_count for page in pages)
    return score >= minimum_score or total_chars < 300, score, warnings


async def select_ocr_candidates(
    session: AsyncSession,
    *,
    strategy: OcrStrategy = "low-text",
    limit: int = 100,
    file_id: int | None = None,
    retry_failed: bool = False,
    source_parser_name: str = PARSER_NAME,
    source_parser_version: str | None = None,
    ocr_parser_name: str = APPLE_VISION_OCR_NAME,
    ocr_parser_version: str | None = None,
) -> list[OcrCandidate]:
    """Select completed PyMuPDF parses that should get an OCR fallback parse."""
    source_version = source_parser_version or get_parser_version()
    ocr_version = ocr_parser_version or get_apple_vision_parser_version()
    source_job = aliased(DocumentParseJobRecord)
    completed_ocr = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .where(DocumentParseJobRecord.parser_name == ocr_parser_name)
        .where(DocumentParseJobRecord.parser_version == ocr_version)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .exists()
    )
    failed_ocr = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .where(DocumentParseJobRecord.parser_name == ocr_parser_name)
        .where(DocumentParseJobRecord.parser_version == ocr_version)
        .where(DocumentParseJobRecord.parse_status == "failed")
        .exists()
    )
    ocr_needed = ~completed_ocr if retry_failed else and_(~completed_ocr, ~failed_ocr)
    stmt = (
        select(source_job, DisclosureFileRecord, DisclosureRecord)
        .join(DisclosureFileRecord, source_job.file_id == DisclosureFileRecord.id)
        .join(DisclosureRecord, DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .where(DisclosureFileRecord.file_type == "pdf")
        .where(DisclosureFileRecord.download_status == "completed")
        .where(source_job.parser_name == source_parser_name)
        .where(source_job.parser_version == source_version)
        .where(source_job.parse_status == "completed")
        .where(source_job.text_path.is_not(None))
        .where(ocr_needed)
    )
    if file_id is not None:
        stmt = stmt.where(DisclosureFileRecord.id == file_id)
    elif strategy == "forecast-correction":
        stmt = stmt.where(
            (DisclosureRecord.title.contains("業績予想"))
            | (DisclosureRecord.title.contains("予想値"))
        )

    stmt = stmt.order_by(source_job.parsed_at.desc(), source_job.id.desc())
    if strategy == "all" or file_id is not None:
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.limit(max(limit * 200, limit))

    candidates: list[OcrCandidate] = []
    rows = (await session.execute(stmt)).all()
    for parse_job, file_record, disclosure in rows:
        text_path = Path(parse_job.text_path)
        pages = tuple(_load_source_pages(text_path))
        should_ocr, score, warnings = needs_ocr(
            pages,
            file_size_bytes=file_record.file_size_bytes,
        )
        if file_id is None and strategy in {"low-text", "forecast-correction"} and not should_ocr:
            continue
        candidates.append(
            OcrCandidate(
                file_record=file_record,
                source_parse_job=parse_job,
                disclosure=disclosure,
                pages=pages,
                suspicion_score=score,
                warnings=warnings,
            )
        )

    if strategy in {"low-text", "forecast-correction"} and file_id is None:
        candidates.sort(key=lambda candidate: candidate.suspicion_score, reverse=True)
    return candidates[:limit]


class AppleVisionOcrProvider:
    """OCR provider backed by macOS Vision through the bundled Swift helper."""

    name = APPLE_VISION_OCR_NAME

    def __init__(
        self,
        *,
        version: str | None = None,
        helper_path: Path | None = None,
        swift_path: str | None = None,
        render_zoom: float = 2.0,
        timeout_seconds: int = 240,
    ) -> None:
        self.version = version or get_apple_vision_parser_version()
        self.helper_path = helper_path or _helper_path()
        self.swift_path = swift_path or shutil.which("swift") or "swift"
        self.render_zoom = render_zoom
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return platform.system() == "Darwin" and shutil.which(self.swift_path) is not None

    def ocr_pdf(self, pdf_path: Path) -> OcrArtifacts:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF does not exist: {pdf_path}")
        if not self.helper_path.exists():
            raise FileNotFoundError(f"Apple Vision helper does not exist: {self.helper_path}")
        if not self.is_available():
            raise RuntimeError("Apple Vision OCR requires macOS and the swift executable")

        output_dir = pdf_path.parent / "parsed"
        image_dir = output_dir / "ocr" / f"{self.name}.{self.version}"
        image_paths = self._render_pdf_pages(pdf_path, image_dir=image_dir)
        raw_pages = self._run_swift_helper(image_paths)
        pages = self._coerce_ocr_pages(raw_pages, image_paths=image_paths)
        markdown = _combined_ocr_markdown(pages)
        markdown_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self.name}.{self.version}"
        markdown_path = output_dir / f"{stem}.md"
        pages_path = output_dir / f"{stem}.pages.json"
        metadata_path = output_dir / f"{stem}.meta.json"

        markdown_path.write_text(markdown, encoding="utf-8")
        pages_path.write_text(json.dumps({"pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
        metadata_path.write_text(
            json.dumps(
                {
                    "parser_name": self.name,
                    "parser_version": self.version,
                    "provider": "Apple Vision",
                    "helper_path": str(self.helper_path),
                    "source_path": str(pdf_path),
                    "image_dir": str(image_dir),
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
        return OcrArtifacts(
            markdown_path=markdown_path,
            pages_path=pages_path,
            metadata_path=metadata_path,
            markdown_sha256=markdown_sha256,
            page_count=len(pages),
            char_count=len(markdown),
        )

    def _render_pdf_pages(self, pdf_path: Path, *, image_dir: Path) -> list[Path]:
        try:
            import pymupdf
        except ImportError:  # pragma: no cover - older PyMuPDF import name
            import fitz as pymupdf

        if hasattr(pymupdf, "TOOLS"):
            pymupdf.TOOLS.mupdf_display_errors(False)
            pymupdf.TOOLS.mupdf_display_warnings(False)

        image_dir.mkdir(parents=True, exist_ok=True)
        document = pymupdf.open(pdf_path)
        image_paths: list[Path] = []
        try:
            matrix = pymupdf.Matrix(self.render_zoom, self.render_zoom)
            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image_path = image_dir / f"page-{page_index + 1:03d}.png"
                pixmap.save(image_path)
                image_paths.append(image_path)
        finally:
            document.close()
        if not image_paths:
            raise RuntimeError(f"PDF has no renderable pages: {pdf_path}")
        return image_paths

    def _run_swift_helper(self, image_paths: list[Path]) -> list[dict[str, Any]]:
        command = [self.swift_path, str(self.helper_path), *[str(path) for path in image_paths]]
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or "unknown Apple Vision OCR error"
            raise RuntimeError(stderr[:4000])
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Apple Vision OCR returned invalid JSON") from exc
        if not isinstance(raw, list):
            raise RuntimeError("Apple Vision OCR returned an unsupported result shape")
        return [page for page in raw if isinstance(page, dict)]

    def _coerce_ocr_pages(
        self,
        raw_pages: list[dict[str, Any]],
        *,
        image_paths: list[Path],
    ) -> list[dict[str, Any]]:
        if len(raw_pages) != len(image_paths):
            raise RuntimeError(
                f"Apple Vision OCR returned {len(raw_pages)} page(s) for {len(image_paths)} rendered image(s)"
            )

        pages: list[dict[str, Any]] = []
        for index, raw_page in enumerate(raw_pages, 1):
            raw_lines = raw_page.get("lines") if isinstance(raw_page.get("lines"), list) else []
            lines = [line for line in raw_lines if isinstance(line, dict)]
            text = normalize_markdown(str(raw_page.get("text") or ""))
            pages.append(
                {
                    "page": index,
                    "markdown": text,
                    "char_count": len(text),
                    "image_path": str(image_paths[index - 1]),
                    "lines": lines,
                }
            )
        return pages


def _combined_ocr_markdown(pages: list[dict[str, Any]]) -> str:
    sections = []
    for page in pages:
        sections.append(f"<!-- page: {page['page']} -->\n\n{str(page['markdown']).strip()}")
    return "\n\n".join(sections).strip() + "\n"


async def ocr_pending_files(
    session: AsyncSession,
    *,
    strategy: OcrStrategy = "low-text",
    limit: int = 100,
    file_id: int | None = None,
    retry_failed: bool = False,
    source_parser_name: str = PARSER_NAME,
    source_parser_version: str | None = None,
    parser_name: str = APPLE_VISION_OCR_NAME,
    parser_version: str | None = None,
    workers: int = 1,
    provider: OcrProvider | None = None,
) -> OcrSummary:
    if workers < 1:
        raise ValueError("workers must be at least 1")

    ocr_provider = provider or AppleVisionOcrProvider(version=parser_version)
    version = parser_version or ocr_provider.version
    if not ocr_provider.is_available():
        raise RuntimeError("Apple Vision OCR is not available in this environment")

    total_candidates = await select_ocr_candidates(
        session,
        strategy=strategy,
        limit=1_000_000,
        file_id=file_id,
        retry_failed=retry_failed,
        source_parser_name=source_parser_name,
        source_parser_version=source_parser_version,
        ocr_parser_name=parser_name,
        ocr_parser_version=version,
    )
    candidates = total_candidates[:limit]
    stats = JobStatsLogger(
        job_name="ocr",
        logger=logger,
        total_items=len(total_candidates),
        scheduled_items=len(candidates),
        workers=workers,
    )
    stats.log_start(
        parser_name=parser_name,
        parser_version=version,
        strategy=strategy,
        limit=limit,
        file_id=file_id,
        retry_failed=retry_failed,
    )

    completed_count = skipped = failed = 0
    work_items: list[tuple[OcrCandidate, DocumentParseJobRecord]] = []
    for candidate in candidates:
        parse_job = await get_or_create_parse_job(
            session,
            file_record=candidate.file_record,
            parser_name=parser_name,
            parser_version=version,
        )
        if parse_job.parse_status == "completed":
            skipped += 1
            stats.record_skipped(item_id=candidate.file_record.id, reason="completed")
            continue
        if parse_job.parse_status == "failed" and not retry_failed:
            skipped += 1
            stats.record_skipped(item_id=candidate.file_record.id, reason="failed")
            continue
        await start_parse_job(session, parse_job)
        work_items.append((candidate, parse_job))

    semaphore = asyncio.Semaphore(min(workers, max(1, len(work_items))))

    async def run_work_item(candidate: OcrCandidate, parse_job: DocumentParseJobRecord):
        async with semaphore:
            item_start = time.perf_counter()
            try:
                artifacts = await asyncio.to_thread(
                    ocr_provider.ocr_pdf,
                    Path(candidate.file_record.storage_path),
                )
                return candidate, parse_job, artifacts, None, time.perf_counter() - item_start
            except Exception as exc:
                return candidate, parse_job, None, exc, time.perf_counter() - item_start

    tasks = [
        asyncio.create_task(run_work_item(candidate, parse_job))
        for candidate, parse_job in work_items
    ]
    for task in asyncio.as_completed(tasks):
        candidate, parse_job, artifacts, error, item_seconds = await task
        if error is not None:
            await fail_parse_job(session, parse_job, str(error))
            failed += 1
            stats.record_failure(
                item_id=candidate.file_record.id,
                item_seconds=item_seconds,
                error=str(error),
                source_parse_job_id=candidate.source_parse_job.id,
                score=candidate.suspicion_score,
                storage_path=candidate.file_record.storage_path,
            )
            continue

        await persist_parse_text_artifacts(
            session,
            parse_job=parse_job,
            markdown_path=artifacts.markdown_path,
            pages_path=artifacts.pages_path,
        )
        await complete_parse_job(
            session,
            parse_job,
            text_path=str(artifacts.markdown_path),
            text_sha256=artifacts.markdown_sha256,
        )
        completed_count += 1
        stats.record_success(
            item_id=candidate.file_record.id,
            item_seconds=item_seconds,
            source_parse_job_id=candidate.source_parse_job.id,
            score=candidate.suspicion_score,
            page_count=artifacts.page_count,
            char_count=artifacts.char_count,
            text_path=artifacts.markdown_path,
        )

    snapshot = stats.log_finish()
    return OcrSummary(
        candidates=len(candidates),
        ocr_completed=completed_count,
        skipped=skipped,
        failed=failed,
        total_pending=len(total_candidates),
        elapsed_seconds=snapshot.elapsed_seconds,
        files_per_second=snapshot.items_per_second,
        average_file_seconds=snapshot.average_item_seconds,
        median_file_seconds=snapshot.median_item_seconds,
        estimated_total_seconds=snapshot.estimated_total_seconds,
        estimated_remaining_seconds=snapshot.estimated_remaining_seconds,
    )
