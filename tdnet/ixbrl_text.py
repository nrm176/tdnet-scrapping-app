"""iXBRL text fallback parsing for TDnet disclosure PDFs."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zipfile import ZipFile

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .orm import DisclosureFileRecord, DisclosureRecord, DocumentParseJobRecord, DocumentParseTextRecord
from .parse_texts import persist_parse_text_artifacts
from .parsers import PARSER_NAME, ParsedPdfArtifacts, get_parser_version, normalize_markdown
from .repository import complete_parse_job, fail_parse_job, get_or_create_parse_job, start_parse_job
from .review import ParsedPage, score_pages
from .stats_logging import JobStatsLogger

logger = logging.getLogger(__name__)

IXBRL_TEXT_PARSER_NAME = "tdnet-ixbrl-text"
IXBRL_TEXT_PARSER_VERSION = "tdnet-1"
IxbrlTextStrategy = Literal["garbled", "forecast-correction", "all"]


@dataclass(frozen=True)
class IxbrlTextCandidate:
    pdf_file: DisclosureFileRecord
    xbrl_file: DisclosureFileRecord
    disclosure: DisclosureRecord
    source_parse_job: DocumentParseJobRecord | None = None
    suspicion_score: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class IxbrlTextSummary:
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


def get_ixbrl_text_parser_version() -> str:
    return IXBRL_TEXT_PARSER_VERSION


def _decode_html(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _select_ixbrl_html_entry(zip_file: ZipFile) -> str:
    entries = [
        info
        for info in zip_file.infolist()
        if not info.is_dir() and info.filename.lower().endswith((".htm", ".html"))
    ]
    if not entries:
        raise ValueError("iXBRL ZIP does not contain an HTML entry")
    entries.sort(key=lambda info: info.file_size, reverse=True)
    return entries[0].filename


def _extract_visible_text(html: str) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    tags_to_remove = []
    for tag in soup.find_all(True):
        tag_name = tag.name or ""
        style = str(tag.get("style") or "").lower().replace(" ", "")
        if (
            tag_name in {"script", "style", "title", "meta", "link"}
            or tag_name in {"ix:header", "ix:hidden", "ix:references", "ix:resources"}
            or "display:none" in style
            or "visibility:hidden" in style
        ):
            tags_to_remove.append(tag)
    for tag in tags_to_remove:
        tag.decompose()
    return normalize_markdown(soup.get_text("\n", strip=True))


def extract_ixbrl_text(zip_path: Path) -> tuple[str, str]:
    if not zip_path.exists():
        raise FileNotFoundError(f"iXBRL ZIP does not exist: {zip_path}")

    with ZipFile(zip_path) as zip_file:
        html_entry = _select_ixbrl_html_entry(zip_file)
        html = _decode_html(zip_file.read(html_entry))
    text = _extract_visible_text(html)
    if not text.strip():
        raise ValueError(f"iXBRL HTML entry has no visible text: {html_entry}")
    return text, html_entry


def parse_ixbrl_zip_to_artifacts(
    *,
    pdf_path: Path,
    xbrl_path: Path,
    parser_name: str = IXBRL_TEXT_PARSER_NAME,
    parser_version: str | None = None,
) -> ParsedPdfArtifacts:
    text, html_entry = extract_ixbrl_text(xbrl_path)
    version = parser_version or get_ixbrl_text_parser_version()
    markdown = f"<!-- page: 1 -->\n\n{text.strip()}\n"
    markdown_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

    output_dir = pdf_path.parent / "parsed"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{parser_name}.{version}"
    markdown_path = output_dir / f"{stem}.md"
    pages_path = output_dir / f"{stem}.pages.json"
    metadata_path = output_dir / f"{stem}.meta.json"
    pages = [{"page": 1, "markdown": text, "char_count": len(text)}]

    markdown_path.write_text(markdown, encoding="utf-8")
    pages_path.write_text(json.dumps({"pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "parser_name": parser_name,
                "parser_version": version,
                "source_path": str(xbrl_path),
                "source_html_entry": html_entry,
                "target_pdf_path": str(pdf_path),
                "markdown_path": str(markdown_path),
                "pages_path": str(pages_path),
                "markdown_sha256": markdown_sha256,
                "page_count": 1,
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
        page_count=1,
        char_count=len(markdown),
    )


def _parsed_pages_from_text(parse_text: DocumentParseTextRecord | None) -> list[ParsedPage]:
    if parse_text is None or not isinstance(parse_text.pages_json, dict):
        return []
    raw_pages = parse_text.pages_json.get("pages")
    if not isinstance(raw_pages, list):
        return []

    pages: list[ParsedPage] = []
    for index, raw_page in enumerate(raw_pages, 1):
        if not isinstance(raw_page, dict):
            continue
        markdown = str(raw_page.get("markdown") or "")
        page_number = raw_page.get("page")
        pages.append(
            ParsedPage(
                page=page_number if isinstance(page_number, int) else index,
                markdown=markdown,
                char_count=int(raw_page.get("char_count") or len(markdown)),
            )
        )
    return pages


async def select_ixbrl_text_candidates(
    session: AsyncSession,
    *,
    strategy: IxbrlTextStrategy = "garbled",
    limit: int = 100,
    file_id: int | None = None,
    retry_failed: bool = False,
    source_parser_name: str = PARSER_NAME,
    source_parser_version: str | None = None,
    parser_name: str = IXBRL_TEXT_PARSER_NAME,
    parser_version: str | None = None,
) -> list[IxbrlTextCandidate]:
    version = parser_version or get_ixbrl_text_parser_version()
    source_version = source_parser_version or get_parser_version()
    pdf_file = aliased(DisclosureFileRecord)
    xbrl_file = aliased(DisclosureFileRecord)
    source_job = aliased(DocumentParseJobRecord)
    source_text = aliased(DocumentParseTextRecord)

    completed_parse = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == pdf_file.id)
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(DocumentParseJobRecord.parser_version == version)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .exists()
    )
    failed_parse = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == pdf_file.id)
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(DocumentParseJobRecord.parser_version == version)
        .where(DocumentParseJobRecord.parse_status == "failed")
        .exists()
    )
    parse_needed = ~completed_parse if retry_failed else and_(~completed_parse, ~failed_parse)
    stmt = (
        select(pdf_file, xbrl_file, DisclosureRecord, source_job, source_text)
        .join(DisclosureRecord, pdf_file.disclosure_id == DisclosureRecord.id)
        .join(
            xbrl_file,
            and_(
                xbrl_file.disclosure_id == DisclosureRecord.id,
                xbrl_file.file_type == "xbrl",
                xbrl_file.download_status == "completed",
            ),
        )
        .outerjoin(
            source_job,
            and_(
                source_job.file_id == pdf_file.id,
                source_job.parser_name == source_parser_name,
                source_job.parser_version == source_version,
                source_job.parse_status == "completed",
            ),
        )
        .outerjoin(source_text, source_text.parse_job_id == source_job.id)
        .where(pdf_file.file_type == "pdf")
        .where(pdf_file.download_status == "completed")
        .where(parse_needed)
        .order_by(pdf_file.downloaded_at.desc().nullslast(), pdf_file.id.desc())
    )
    if file_id is not None:
        stmt = stmt.where(pdf_file.id == file_id)
    elif strategy == "forecast-correction":
        stmt = stmt.where(
            (DisclosureRecord.title.contains("業績予想"))
            | (DisclosureRecord.title.contains("予想値"))
        )
    if strategy == "garbled" and file_id is None:
        stmt = stmt.where(source_job.id.is_not(None), source_text.id.is_not(None))

    rows = (await session.execute(stmt.limit(limit * 200 if file_id is None else limit))).all()
    candidates: list[IxbrlTextCandidate] = []
    for pdf_record, xbrl_record, disclosure, parse_job, parse_text in rows:
        pages = _parsed_pages_from_text(parse_text)
        score, warnings = score_pages(pages, file_size_bytes=pdf_record.file_size_bytes)
        if file_id is None and strategy == "garbled" and score < 40:
            continue
        candidates.append(
            IxbrlTextCandidate(
                pdf_file=pdf_record,
                xbrl_file=xbrl_record,
                disclosure=disclosure,
                source_parse_job=parse_job,
                suspicion_score=score,
                warnings=warnings,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


async def parse_ixbrl_text_pending(
    session: AsyncSession,
    *,
    strategy: IxbrlTextStrategy = "garbled",
    limit: int = 100,
    file_id: int | None = None,
    retry_failed: bool = False,
    source_parser_name: str = PARSER_NAME,
    source_parser_version: str | None = None,
    parser_name: str = IXBRL_TEXT_PARSER_NAME,
    parser_version: str | None = None,
) -> IxbrlTextSummary:
    version = parser_version or get_ixbrl_text_parser_version()
    total_candidates = await select_ixbrl_text_candidates(
        session,
        strategy=strategy,
        limit=1_000_000,
        file_id=file_id,
        retry_failed=retry_failed,
        source_parser_name=source_parser_name,
        source_parser_version=source_parser_version,
        parser_name=parser_name,
        parser_version=version,
    )
    candidates = total_candidates[:limit]
    parsed = skipped = failed = 0
    stats = JobStatsLogger(
        job_name="ixbrl_text",
        logger=logger,
        total_items=len(total_candidates),
        scheduled_items=len(candidates),
        workers=1,
    )
    stats.log_start(
        parser_name=parser_name,
        parser_version=version,
        source_parser_name=source_parser_name,
        source_parser_version=source_parser_version or get_parser_version(),
        strategy=strategy,
        limit=limit,
        file_id=file_id,
        retry_failed=retry_failed,
    )

    for candidate in candidates:
        item_start = time.perf_counter()
        parse_job = await get_or_create_parse_job(
            session,
            file_record=candidate.pdf_file,
            parser_name=parser_name,
            parser_version=version,
        )
        if parse_job.parse_status == "completed":
            skipped += 1
            stats.record_skipped(item_id=candidate.pdf_file.id, reason="completed")
            continue
        if parse_job.parse_status == "failed" and not retry_failed:
            skipped += 1
            stats.record_skipped(item_id=candidate.pdf_file.id, reason="failed")
            continue

        await start_parse_job(session, parse_job)
        try:
            artifacts = parse_ixbrl_zip_to_artifacts(
                pdf_path=Path(candidate.pdf_file.storage_path),
                xbrl_path=Path(candidate.xbrl_file.storage_path),
                parser_name=parser_name,
                parser_version=version,
            )
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
            parsed += 1
            stats.record_success(
                item_id=candidate.pdf_file.id,
                item_seconds=time.perf_counter() - item_start,
                xbrl_file_id=candidate.xbrl_file.id,
                source_parse_job_id=candidate.source_parse_job.id if candidate.source_parse_job else None,
                score=candidate.suspicion_score,
                page_count=artifacts.page_count,
                char_count=artifacts.char_count,
                text_path=artifacts.markdown_path,
            )
        except Exception as exc:
            await fail_parse_job(session, parse_job, str(exc))
            failed += 1
            stats.record_failure(
                item_id=candidate.pdf_file.id,
                item_seconds=time.perf_counter() - item_start,
                error=str(exc),
                xbrl_path=candidate.xbrl_file.storage_path,
                pdf_path=candidate.pdf_file.storage_path,
            )

    snapshot = stats.log_finish()
    return IxbrlTextSummary(
        candidates=len(candidates),
        parsed=parsed,
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
