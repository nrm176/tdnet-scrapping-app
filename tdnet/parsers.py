"""PDF parsing services for completed TDnet disclosure files."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .repository import (
    complete_parse_job,
    fail_parse_job,
    get_or_create_parse_job,
    iter_files_for_parse,
    start_parse_job,
)

PARSER_NAME = "pymupdf4llm"
NORMALIZER_VERSION = "tdnet-1"


@dataclass(frozen=True)
class ParseSummary:
    candidates: int = 0
    parsed: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ParsedPdfArtifacts:
    markdown_path: Path
    pages_path: Path
    metadata_path: Path
    markdown_sha256: str
    page_count: int
    char_count: int


def get_parser_version() -> str:
    """Return the parser identity used to decide whether a file is already parsed."""
    try:
        package_version = metadata.version("pymupdf4llm")
    except metadata.PackageNotFoundError:
        package_version = "unknown"
    return f"{package_version}+{NORMALIZER_VERSION}"


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


async def parse_pending_files(
    session: AsyncSession,
    *,
    limit: int = 100,
    retry_failed: bool = False,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
) -> ParseSummary:
    version = parser_version or get_parser_version()
    files = await iter_files_for_parse(
        session,
        parser_name=parser_name,
        parser_version=version,
        limit=limit,
        retry_failed=retry_failed,
    )
    parsed = skipped = failed = 0

    for file_record in files:
        parse_job = await get_or_create_parse_job(
            session,
            file_record=file_record,
            parser_name=parser_name,
            parser_version=version,
        )
        if parse_job.parse_status == "completed":
            skipped += 1
            continue
        if parse_job.parse_status == "failed" and not retry_failed:
            skipped += 1
            continue

        await start_parse_job(session, parse_job)
        try:
            artifacts = parse_pdf_to_artifacts(
                Path(file_record.storage_path),
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
        except Exception as exc:
            await fail_parse_job(session, parse_job, str(exc))
            failed += 1

    return ParseSummary(candidates=len(files), parsed=parsed, skipped=skipped, failed=failed)
