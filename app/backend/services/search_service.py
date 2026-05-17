"""Database-backed search and document retrieval for parsed TDnet text."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.schemas import (
    ParsedPageResponse,
    ParserOption,
    ParseJobDetailResponse,
    ParseSearchResult,
    ParseSearchResponse,
)
from tdnet.orm import (
    DisclosureFileRecord,
    DisclosureRecord,
    DocumentParseJobRecord,
    DocumentParseTextRecord,
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _make_snippet(text: str, query: str | None, *, radius: int = 160) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    if not query:
        return normalized[: radius * 2]

    index = normalized.lower().find(query.lower())
    if index < 0:
        return normalized[: radius * 2]
    start = max(0, index - radius)
    end = min(len(normalized), index + len(query) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized) else ""
    return f"{prefix}{normalized[start:end]}{suffix}"


def _extract_pages(pages_json: dict | None) -> list[ParsedPageResponse]:
    if not isinstance(pages_json, dict):
        return []
    raw_pages = pages_json.get("pages")
    if not isinstance(raw_pages, list):
        return []

    pages: list[ParsedPageResponse] = []
    for index, raw_page in enumerate(raw_pages, 1):
        if not isinstance(raw_page, dict):
            continue
        markdown = str(raw_page.get("markdown") or "")
        page_number = raw_page.get("page")
        pages.append(
            ParsedPageResponse(
                page=page_number if isinstance(page_number, int) else index,
                markdown=markdown,
                char_count=int(raw_page.get("char_count") or len(markdown)),
            )
        )
    return pages


def _base_join() -> Select:
    return (
        select(DocumentParseJobRecord, DocumentParseTextRecord, DisclosureFileRecord, DisclosureRecord)
        .join(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
        .join(DisclosureFileRecord, DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .join(DisclosureRecord, DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .where(DocumentParseJobRecord.parse_status == "completed")
    )


def _apply_filters(
    stmt: Select,
    *,
    query: str | None = None,
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select:
    if parser_name:
        stmt = stmt.where(DocumentParseJobRecord.parser_name == parser_name)
    if parser_version:
        stmt = stmt.where(DocumentParseJobRecord.parser_version == parser_version)
    if code:
        stmt = stmt.where(DisclosureRecord.code == code.strip().upper())
    if date_from:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    if query:
        pattern = f"%{_escape_like(query.strip())}%"
        stmt = stmt.where(
            or_(
                DocumentParseTextRecord.content_text.ilike(pattern, escape="\\"),
                DisclosureRecord.title.ilike(pattern, escape="\\"),
                DisclosureRecord.name.ilike(pattern, escape="\\"),
                DisclosureRecord.code.ilike(pattern, escape="\\"),
            )
        )
    return stmt


def _row_to_result(
    parse_job: DocumentParseJobRecord,
    parse_text: DocumentParseTextRecord,
    file_record: DisclosureFileRecord,
    disclosure: DisclosureRecord,
    *,
    query: str | None,
) -> ParseSearchResult:
    return ParseSearchResult(
        parse_job_id=parse_job.id,
        file_id=file_record.id,
        disclosure_id=disclosure.id,
        disclosure_date=disclosure.disclosure_date,
        time=disclosure.time,
        code=disclosure.code,
        company_name=disclosure.name,
        title=disclosure.title,
        parser_name=parse_job.parser_name,
        parser_version=parse_job.parser_version,
        page_count=parse_text.page_count,
        char_count=parse_text.char_count,
        parsed_at=parse_job.parsed_at,
        snippet=_make_snippet(parse_text.content_text, query),
    )


async def list_parser_options(session: AsyncSession) -> list[ParserOption]:
    stmt = (
        select(
            DocumentParseJobRecord.parser_name,
            DocumentParseJobRecord.parser_version,
            func.count(DocumentParseJobRecord.id),
            func.count(DocumentParseTextRecord.id),
        )
        .outerjoin(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .group_by(DocumentParseJobRecord.parser_name, DocumentParseJobRecord.parser_version)
        .order_by(DocumentParseJobRecord.parser_name.asc(), DocumentParseJobRecord.parser_version.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        ParserOption(
            parser_name=parser_name,
            parser_version=parser_version,
            parse_jobs=int(parse_jobs or 0),
            parse_texts=int(parse_texts or 0),
        )
        for parser_name, parser_version, parse_jobs, parse_texts in rows
    ]


async def search_parse_texts(
    session: AsyncSession,
    *,
    query: str | None = None,
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 25,
    offset: int = 0,
) -> ParseSearchResponse:
    normalized_query = query.strip() if query and query.strip() else None
    filtered = _apply_filters(
        _base_join(),
        query=normalized_query,
        parser_name=parser_name,
        parser_version=parser_version,
        code=code,
        date_from=date_from,
        date_to=date_to,
    )
    count_stmt = filtered.with_only_columns(func.count()).order_by(None)
    total = int(await session.scalar(count_stmt) or 0)
    rows = (
        await session.execute(
            filtered.order_by(
                DisclosureRecord.disclosure_date.desc(),
                DisclosureRecord.time.desc(),
                DocumentParseJobRecord.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return ParseSearchResponse(
        query=normalized_query,
        total=total,
        limit=limit,
        offset=offset,
        results=[
            _row_to_result(parse_job, parse_text, file_record, disclosure, query=normalized_query)
            for parse_job, parse_text, file_record, disclosure in rows
        ],
    )


async def get_parse_job_detail(
    session: AsyncSession,
    parse_job_id: int,
) -> ParseJobDetailResponse | None:
    stmt = _base_join().where(DocumentParseJobRecord.id == parse_job_id)
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    parse_job, parse_text, file_record, disclosure = row
    base = _row_to_result(parse_job, parse_text, file_record, disclosure, query=None)
    return ParseJobDetailResponse(
        **base.model_dump(),
        source_url=file_record.source_url,
        pdf_path=file_record.storage_path,
        text_path=parse_job.text_path,
        content_text=parse_text.content_text,
        pages=_extract_pages(parse_text.pages_json),
    )
