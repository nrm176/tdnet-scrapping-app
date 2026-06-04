"""Database-backed search and document retrieval for parsed TDnet text."""
from __future__ import annotations

from datetime import date
from typing import Literal, Sequence

from sqlalchemy import Select, and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.backend.schemas import (
    ParsedPageResponse,
    ParserOption,
    ParseJobDetailResponse,
    ParseSearchResult,
    ParseSearchResponse,
    ReportTagAssignmentResponse,
    ReportCalendarDay,
    ReportTagResponse,
)
from tdnet.tagging import (
    list_report_tag_summaries,
    list_tag_assignments_for_disclosures,
    normalize_tag_slugs,
    tag_assignment_exists,
)
from tdnet.orm import (
    DisclosureFileRecord,
    DisclosureRecord,
    DocumentParseJobRecord,
    DocumentParseTextRecord,
)
from tdnet.ixbrl_text import IXBRL_TEXT_PARSER_NAME
from tdnet.ocr import APPLE_VISION_OCR_NAME
from tdnet.parsers import PARSER_NAME

TagMode = Literal["any", "all"]
PARSER_PRIORITY = {
    IXBRL_TEXT_PARSER_NAME: 30,
    APPLE_VISION_OCR_NAME: 20,
    PARSER_NAME: 10,
}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_pattern(value: str) -> str:
    return f"%{_escape_like(value.strip())}%"


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


def _parser_priority_expr(parse_job_model):
    return case(
        *[
            (parse_job_model.parser_name == parser_name, priority)
            for parser_name, priority in PARSER_PRIORITY.items()
        ],
        else_=0,
    )


def _prefer_best_parse_per_file(stmt: Select) -> Select:
    other_job = aliased(DocumentParseJobRecord)
    other_text = aliased(DocumentParseTextRecord)
    current_priority = _parser_priority_expr(DocumentParseJobRecord)
    other_priority = _parser_priority_expr(other_job)
    better_parse_exists = (
        select(other_job.id)
        .join(other_text, other_text.parse_job_id == other_job.id)
        .where(other_job.file_id == DocumentParseJobRecord.file_id)
        .where(other_job.parse_status == "completed")
        .where(
            or_(
                other_priority > current_priority,
                and_(other_priority == current_priority, other_job.id > DocumentParseJobRecord.id),
            )
        )
        .exists()
    )
    return stmt.where(~better_parse_exists)


def _apply_filters(
    stmt: Select,
    *,
    query: str | None = None,
    title_query: str | None = None,
    text_query: str | None = None,
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tags: Sequence[str] | None = None,
    tag_mode: TagMode = "any",
) -> Select:
    if parser_name:
        stmt = stmt.where(DocumentParseJobRecord.parser_name == parser_name)
    if parser_version:
        stmt = stmt.where(DocumentParseJobRecord.parser_version == parser_version)
    if not parser_name and not parser_version:
        stmt = _prefer_best_parse_per_file(stmt)
    if code:
        stmt = stmt.where(DisclosureRecord.code == code.strip().upper())
    if date_from:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    stmt = _apply_disclosure_tag_filters(stmt, tags=tags, tag_mode=tag_mode)
    if title_query:
        stmt = stmt.where(DisclosureRecord.title.ilike(_like_pattern(title_query), escape="\\"))
    if text_query:
        stmt = stmt.where(DocumentParseTextRecord.content_text.ilike(_like_pattern(text_query), escape="\\"))
    if query:
        pattern = _like_pattern(query)
        stmt = stmt.where(
            or_(
                DocumentParseTextRecord.content_text.ilike(pattern, escape="\\"),
                DisclosureRecord.title.ilike(pattern, escape="\\"),
                DisclosureRecord.name.ilike(pattern, escape="\\"),
                DisclosureRecord.code.ilike(pattern, escape="\\"),
            )
        )
    return stmt


def _apply_disclosure_tag_filters(
    stmt: Select,
    *,
    tags: Sequence[str] | None = None,
    tag_mode: TagMode = "any",
) -> Select:
    clauses = tag_assignment_exists(DisclosureRecord.id, normalize_tag_slugs(tags), tag_mode)
    if not clauses:
        return stmt
    for clause in clauses:
        stmt = stmt.where(clause)
    return stmt


def _tag_views_to_response(tags: list) -> list[ReportTagAssignmentResponse]:
    return [
        ReportTagAssignmentResponse(
            slug=tag.slug,
            label_ja=tag.label_ja,
            label_en=tag.label_en,
            is_primary=tag.is_primary,
            confidence=tag.confidence,
            source=tag.source,
        )
        for tag in tags
    ]


def _row_to_result(
    parse_job: DocumentParseJobRecord,
    parse_text: DocumentParseTextRecord,
    file_record: DisclosureFileRecord,
    disclosure: DisclosureRecord,
    *,
    query: str | None,
    snippet_query: str | None = None,
    tags: list[ReportTagAssignmentResponse] | None = None,
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
        snippet=_make_snippet(parse_text.content_text, snippet_query or query),
        tags=tags or [],
    )


async def list_report_tags(session: AsyncSession) -> list[ReportTagResponse]:
    summaries = await list_report_tag_summaries(session)
    return [
        ReportTagResponse(
            slug=summary.slug,
            label_ja=summary.label_ja,
            label_en=summary.label_en,
            description=summary.description,
            priority=summary.priority,
            active=summary.active,
            assignment_count=summary.assignment_count,
            primary_count=summary.primary_count,
        )
        for summary in summaries
    ]


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
    title_query: str | None = None,
    text_query: str | None = None,
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tags: Sequence[str] | None = None,
    tag_mode: TagMode = "any",
    limit: int = 25,
    offset: int = 0,
) -> ParseSearchResponse:
    normalized_query = query.strip() if query and query.strip() else None
    normalized_title_query = title_query.strip() if title_query and title_query.strip() else None
    normalized_text_query = text_query.strip() if text_query and text_query.strip() else None
    filtered = _apply_filters(
        _base_join(),
        query=normalized_query,
        title_query=normalized_title_query,
        text_query=normalized_text_query,
        parser_name=parser_name,
        parser_version=parser_version,
        code=code,
        date_from=date_from,
        date_to=date_to,
        tags=tags,
        tag_mode=tag_mode,
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
    disclosure_ids = [disclosure.id for _, _, _, disclosure in rows]
    tag_map = await list_tag_assignments_for_disclosures(session, disclosure_ids)
    return ParseSearchResponse(
        query=normalized_query,
        total=total,
        limit=limit,
        offset=offset,
        results=[
            _row_to_result(
                parse_job,
                parse_text,
                file_record,
                disclosure,
                query=normalized_query,
                snippet_query=normalized_text_query,
                tags=_tag_views_to_response(tag_map.get(disclosure.id, [])),
            )
            for parse_job, parse_text, file_record, disclosure in rows
        ],
    )


async def list_report_calendar_days(
    session: AsyncSession,
    *,
    month_start: date,
    month_end: date,
    query: str | None = None,
    title_query: str | None = None,
    text_query: str | None = None,
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    tags: Sequence[str] | None = None,
    tag_mode: TagMode = "any",
) -> list[ReportCalendarDay]:
    record_stmt = (
        select(
            DisclosureRecord.disclosure_date,
            func.count(DisclosureRecord.id),
        )
        .where(DisclosureRecord.disclosure_date >= month_start)
        .where(DisclosureRecord.disclosure_date <= month_end)
        .group_by(DisclosureRecord.disclosure_date)
    )
    if code:
        record_stmt = record_stmt.where(DisclosureRecord.code == code.strip().upper())
    if title_query and title_query.strip():
        record_stmt = record_stmt.where(DisclosureRecord.title.ilike(_like_pattern(title_query), escape="\\"))
    record_stmt = _apply_disclosure_tag_filters(record_stmt, tags=tags, tag_mode=tag_mode)

    record_counts = {
        disclosure_date: int(record_count or 0)
        for disclosure_date, record_count in (await session.execute(record_stmt)).all()
    }

    normalized_query = query.strip() if query and query.strip() else None
    normalized_title_query = title_query.strip() if title_query and title_query.strip() else None
    normalized_text_query = text_query.strip() if text_query and text_query.strip() else None
    filtered = _apply_filters(
        _base_join(),
        query=normalized_query,
        title_query=normalized_title_query,
        text_query=normalized_text_query,
        parser_name=parser_name,
        parser_version=parser_version,
        code=code,
        date_from=month_start,
        date_to=month_end,
        tags=tags,
        tag_mode=tag_mode,
    )
    stmt = (
        filtered.with_only_columns(
            DisclosureRecord.disclosure_date,
            func.count(distinct(DisclosureFileRecord.id)),
        )
        .group_by(DisclosureRecord.disclosure_date)
        .order_by(DisclosureRecord.disclosure_date.asc())
    )
    report_counts = {
        disclosure_date: int(report_count or 0)
        for disclosure_date, report_count in (await session.execute(stmt)).all()
    }
    return [
        ReportCalendarDay(
            disclosure_date=disclosure_date,
            record_count=record_counts.get(disclosure_date, 0),
            report_count=report_counts.get(disclosure_date, 0),
        )
        for disclosure_date in sorted(record_counts.keys() | report_counts.keys())
    ]


async def get_parse_job_detail(
    session: AsyncSession,
    parse_job_id: int,
) -> ParseJobDetailResponse | None:
    stmt = _base_join().where(DocumentParseJobRecord.id == parse_job_id)
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    parse_job, parse_text, file_record, disclosure = row
    tag_map = await list_tag_assignments_for_disclosures(session, [disclosure.id])
    base = _row_to_result(
        parse_job,
        parse_text,
        file_record,
        disclosure,
        query=None,
        tags=_tag_views_to_response(tag_map.get(disclosure.id, [])),
    )
    return ParseJobDetailResponse(
        **base.model_dump(),
        source_url=file_record.source_url,
        pdf_path=file_record.storage_path,
        text_path=parse_job.text_path,
        content_text=parse_text.content_text,
        pages=_extract_pages(parse_text.pages_json),
    )
