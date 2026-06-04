"""Database-backed search and document retrieval for parsed TDnet text."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Literal, Sequence

from sqlalchemy import Select, and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.backend.schemas import (
    CompanyTimelineDisclosureResponse,
    CompanyTimelineFileResponse,
    CompanyTimelineParserResponse,
    CompanyTimelineResponse,
    ParsedPageResponse,
    ParserFallbackCandidate,
    ParserOption,
    ParseJobDetailResponse,
    ParserQualityParser,
    ParserQualityResponse,
    ParserRecentError,
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
from tdnet.ixbrl_text import IXBRL_TEXT_PARSER_NAME, get_ixbrl_text_parser_version
from tdnet.ocr import APPLE_VISION_OCR_NAME, get_apple_vision_parser_version
from tdnet.parsers import PARSER_NAME, get_parser_version

TagMode = Literal["any", "all"]
PARSER_PRIORITY = {
    IXBRL_TEXT_PARSER_NAME: 30,
    APPLE_VISION_OCR_NAME: 20,
    PARSER_NAME: 10,
}
LOW_TEXT_CHAR_THRESHOLD = 300


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


def _timeline_parse_matches(
    parse_job: DocumentParseJobRecord,
    parse_text: DocumentParseTextRecord | None,
    *,
    parser_name: str | None = None,
    parser_version: str | None = None,
    text_query: str | None = None,
) -> bool:
    if parse_job.parse_status != "completed" or parse_text is None:
        return False
    if parser_name and parse_job.parser_name != parser_name:
        return False
    if parser_version and parse_job.parser_version != parser_version:
        return False
    if text_query and text_query.lower() not in parse_text.content_text.lower():
        return False
    return True


def _select_timeline_snippet(
    rows: list[tuple[DocumentParseJobRecord, DocumentParseTextRecord | None]],
    *,
    parser_name: str | None = None,
    parser_version: str | None = None,
    text_query: str | None = None,
) -> tuple[int | None, str]:
    candidates = [
        (parse_job, parse_text)
        for parse_job, parse_text in rows
        if _timeline_parse_matches(
            parse_job,
            parse_text,
            parser_name=parser_name,
            parser_version=parser_version,
            text_query=text_query,
        )
    ]
    if not candidates and not parser_name and not parser_version and not text_query:
        candidates = [
            (parse_job, parse_text)
            for parse_job, parse_text in rows
            if parse_job.parse_status == "completed" and parse_text is not None
        ]
    if not candidates:
        return None, ""

    candidates.sort(
        key=lambda row: (
            PARSER_PRIORITY.get(row[0].parser_name, 0),
            row[0].id,
        ),
        reverse=True,
    )
    parse_job, parse_text = candidates[0]
    return parse_job.id, _make_snippet(parse_text.content_text, text_query)


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


def _sum_when(condition) -> object:
    return func.sum(case((condition, 1), else_=0))


async def _count_ocr_low_text_candidates(session: AsyncSession) -> int:
    source_job = aliased(DocumentParseJobRecord)
    source_text = aliased(DocumentParseTextRecord)
    completed_ocr_job = aliased(DocumentParseJobRecord)
    source_version = get_parser_version()
    ocr_version = get_apple_vision_parser_version()
    stmt = (
        select(func.count(distinct(source_job.id)))
        .join(DisclosureFileRecord, source_job.file_id == DisclosureFileRecord.id)
        .join(source_text, source_text.parse_job_id == source_job.id)
        .outerjoin(
            completed_ocr_job,
            and_(
                completed_ocr_job.file_id == source_job.file_id,
                completed_ocr_job.parser_name == APPLE_VISION_OCR_NAME,
                completed_ocr_job.parser_version == ocr_version,
                completed_ocr_job.parse_status == "completed",
            ),
        )
        .where(DisclosureFileRecord.file_type == "pdf")
        .where(DisclosureFileRecord.download_status == "completed")
        .where(source_job.parser_name == PARSER_NAME)
        .where(source_job.parser_version == source_version)
        .where(source_job.parse_status == "completed")
        .where(source_text.char_count < LOW_TEXT_CHAR_THRESHOLD)
        .where(completed_ocr_job.id.is_(None))
    )
    return int(await session.scalar(stmt) or 0)


async def _count_ixbrl_sidecar_candidates(session: AsyncSession) -> int:
    pdf_file = aliased(DisclosureFileRecord)
    xbrl_file = aliased(DisclosureFileRecord)
    completed_ixbrl_job = aliased(DocumentParseJobRecord)
    ixbrl_version = get_ixbrl_text_parser_version()
    stmt = (
        select(func.count(distinct(pdf_file.id)))
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
            completed_ixbrl_job,
            and_(
                completed_ixbrl_job.file_id == pdf_file.id,
                completed_ixbrl_job.parser_name == IXBRL_TEXT_PARSER_NAME,
                completed_ixbrl_job.parser_version == ixbrl_version,
                completed_ixbrl_job.parse_status == "completed",
            ),
        )
        .where(pdf_file.file_type == "pdf")
        .where(pdf_file.download_status == "completed")
        .where(completed_ixbrl_job.id.is_(None))
    )
    return int(await session.scalar(stmt) or 0)


async def get_parser_quality(session: AsyncSession) -> ParserQualityResponse:
    parser_rows = (
        await session.execute(
            select(
                DocumentParseJobRecord.parser_name,
                DocumentParseJobRecord.parser_version,
                func.count(DocumentParseJobRecord.id),
                _sum_when(DocumentParseJobRecord.parse_status == "completed"),
                _sum_when(DocumentParseJobRecord.parse_status == "failed"),
                _sum_when(~DocumentParseJobRecord.parse_status.in_(["completed", "failed"])),
                func.count(DocumentParseTextRecord.id),
                _sum_when(DocumentParseTextRecord.char_count < LOW_TEXT_CHAR_THRESHOLD),
                func.avg(DocumentParseTextRecord.char_count),
            )
            .outerjoin(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
            .group_by(DocumentParseJobRecord.parser_name, DocumentParseJobRecord.parser_version)
            .order_by(DocumentParseJobRecord.parser_name.asc(), DocumentParseJobRecord.parser_version.desc())
        )
    ).all()
    parsers = [
        ParserQualityParser(
            parser_name=parser_name,
            parser_version=parser_version,
            total_jobs=int(total_jobs or 0),
            completed_jobs=int(completed_jobs or 0),
            failed_jobs=int(failed_jobs or 0),
            pending_jobs=int(pending_jobs or 0),
            parse_texts=int(parse_texts or 0),
            low_text_jobs=int(low_text_jobs or 0),
            average_char_count=float(average_char_count) if average_char_count is not None else None,
        )
        for (
            parser_name,
            parser_version,
            total_jobs,
            completed_jobs,
            failed_jobs,
            pending_jobs,
            parse_texts,
            low_text_jobs,
            average_char_count,
        ) in parser_rows
    ]
    error_rows = (
        await session.execute(
            select(
                DocumentParseJobRecord.id,
                DocumentParseJobRecord.file_id,
                DocumentParseJobRecord.parser_name,
                DocumentParseJobRecord.parser_version,
                DocumentParseJobRecord.last_parse_error,
                DocumentParseJobRecord.updated_at,
            )
            .where(DocumentParseJobRecord.parse_status == "failed")
            .where(DocumentParseJobRecord.last_parse_error.is_not(None))
            .order_by(DocumentParseJobRecord.updated_at.desc(), DocumentParseJobRecord.id.desc())
            .limit(5)
        )
    ).all()
    return ParserQualityResponse(
        total_jobs=sum(parser.total_jobs for parser in parsers),
        completed_jobs=sum(parser.completed_jobs for parser in parsers),
        failed_jobs=sum(parser.failed_jobs for parser in parsers),
        parse_texts=sum(parser.parse_texts for parser in parsers),
        low_text_jobs=sum(parser.low_text_jobs for parser in parsers),
        fallback_candidates=[
            ParserFallbackCandidate(
                name="Low-text PDF OCR",
                parser_name=APPLE_VISION_OCR_NAME,
                parser_version=get_apple_vision_parser_version(),
                candidate_count=await _count_ocr_low_text_candidates(session),
                description="Completed current PyMuPDF PDF parses below 300 characters without current OCR output.",
            ),
            ParserFallbackCandidate(
                name="PDF/XBRL sidecar text",
                parser_name=IXBRL_TEXT_PARSER_NAME,
                parser_version=get_ixbrl_text_parser_version(),
                candidate_count=await _count_ixbrl_sidecar_candidates(session),
                description="Completed PDF/XBRL pairs without current iXBRL text output.",
            ),
        ],
        parsers=parsers,
        recent_errors=[
            ParserRecentError(
                parse_job_id=parse_job_id,
                file_id=file_id,
                parser_name=parser_name,
                parser_version=parser_version,
                error=str(error or ""),
                updated_at=updated_at,
            )
            for parse_job_id, file_id, parser_name, parser_version, error, updated_at in error_rows
        ],
    )


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


async def get_company_timeline(
    session: AsyncSession,
    *,
    code: str,
    title_query: str | None = None,
    text_query: str | None = None,
    parser_name: str | None = None,
    parser_version: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tags: Sequence[str] | None = None,
    tag_mode: TagMode = "any",
    order: Literal["asc", "desc"] = "desc",
    limit: int = 50,
    offset: int = 0,
) -> CompanyTimelineResponse:
    normalized_code = code.strip().upper()
    normalized_title_query = title_query.strip() if title_query and title_query.strip() else None
    normalized_text_query = text_query.strip() if text_query and text_query.strip() else None

    disclosure_stmt = select(DisclosureRecord).where(DisclosureRecord.code == normalized_code)
    if date_from:
        disclosure_stmt = disclosure_stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to:
        disclosure_stmt = disclosure_stmt.where(DisclosureRecord.disclosure_date <= date_to)
    if normalized_title_query:
        disclosure_stmt = disclosure_stmt.where(
            DisclosureRecord.title.ilike(_like_pattern(normalized_title_query), escape="\\")
        )
    disclosure_stmt = _apply_disclosure_tag_filters(disclosure_stmt, tags=tags, tag_mode=tag_mode)

    if normalized_text_query or parser_name or parser_version:
        parse_filter = _apply_filters(
            _base_join(),
            title_query=normalized_title_query,
            text_query=normalized_text_query,
            parser_name=parser_name,
            parser_version=parser_version,
            code=normalized_code,
            date_from=date_from,
            date_to=date_to,
            tags=tags,
            tag_mode=tag_mode,
        )
        matching_disclosure_ids = parse_filter.with_only_columns(DisclosureRecord.id).distinct().subquery()
        disclosure_stmt = disclosure_stmt.where(
            DisclosureRecord.id.in_(select(matching_disclosure_ids.c.id))
        )

    total = int(await session.scalar(select(func.count()).select_from(disclosure_stmt.order_by(None).subquery())) or 0)
    if order == "asc":
        order_by = (
            DisclosureRecord.disclosure_date.asc(),
            DisclosureRecord.time.asc(),
            DisclosureRecord.id.asc(),
        )
    else:
        order_by = (
            DisclosureRecord.disclosure_date.desc(),
            DisclosureRecord.time.desc(),
            DisclosureRecord.id.desc(),
        )

    disclosures = (
        (await session.execute(disclosure_stmt.order_by(*order_by).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    company_name = disclosures[0].name if disclosures else await session.scalar(
        select(DisclosureRecord.name)
        .where(DisclosureRecord.code == normalized_code)
        .order_by(DisclosureRecord.disclosure_date.desc(), DisclosureRecord.time.desc())
        .limit(1)
    )
    if not disclosures:
        return CompanyTimelineResponse(
            code=normalized_code,
            company_name=company_name,
            total=total,
            limit=limit,
            offset=offset,
            results=[],
        )

    disclosure_ids = [disclosure.id for disclosure in disclosures]
    tag_map = await list_tag_assignments_for_disclosures(session, disclosure_ids)

    file_rows = (
        (
            await session.execute(
                select(DisclosureFileRecord)
                .where(DisclosureFileRecord.disclosure_id.in_(disclosure_ids))
                .order_by(
                    DisclosureFileRecord.disclosure_id.asc(),
                    DisclosureFileRecord.file_type.asc(),
                    DisclosureFileRecord.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    files_by_disclosure: defaultdict[str, list[DisclosureFileRecord]] = defaultdict(list)
    file_disclosure_ids: dict[int, str] = {}
    for file_record in file_rows:
        files_by_disclosure[file_record.disclosure_id].append(file_record)
        file_disclosure_ids[file_record.id] = file_record.disclosure_id

    parse_rows_by_disclosure: defaultdict[str, list[tuple[DocumentParseJobRecord, DocumentParseTextRecord | None]]] = (
        defaultdict(list)
    )
    if file_disclosure_ids:
        parse_rows = (
            await session.execute(
                select(DocumentParseJobRecord, DocumentParseTextRecord)
                .outerjoin(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
                .where(DocumentParseJobRecord.file_id.in_(list(file_disclosure_ids)))
                .order_by(
                    DocumentParseJobRecord.file_id.asc(),
                    _parser_priority_expr(DocumentParseJobRecord).desc(),
                    DocumentParseJobRecord.id.desc(),
                )
            )
        ).all()
        for parse_job, parse_text in parse_rows:
            disclosure_id = file_disclosure_ids.get(parse_job.file_id)
            if disclosure_id is not None:
                parse_rows_by_disclosure[disclosure_id].append((parse_job, parse_text))

    results: list[CompanyTimelineDisclosureResponse] = []
    for disclosure in disclosures:
        parse_rows = parse_rows_by_disclosure.get(disclosure.id, [])
        best_parse_job_id, snippet = _select_timeline_snippet(
            parse_rows,
            parser_name=parser_name,
            parser_version=parser_version,
            text_query=normalized_text_query,
        )
        results.append(
            CompanyTimelineDisclosureResponse(
                disclosure_id=disclosure.id,
                disclosure_date=disclosure.disclosure_date,
                time=disclosure.time,
                code=disclosure.code,
                company_name=disclosure.name,
                title=disclosure.title,
                xbrl_available=disclosure.xbrl_available,
                tags=_tag_views_to_response(tag_map.get(disclosure.id, [])),
                files=[
                    CompanyTimelineFileResponse(
                        file_id=file_record.id,
                        file_type=file_record.file_type,
                        download_status=file_record.download_status,
                        file_size_bytes=file_record.file_size_bytes,
                        downloaded_at=file_record.downloaded_at,
                        source_url=file_record.source_url,
                        storage_path=file_record.storage_path,
                    )
                    for file_record in files_by_disclosure.get(disclosure.id, [])
                ],
                parsers=[
                    CompanyTimelineParserResponse(
                        parse_job_id=parse_job.id,
                        file_id=parse_job.file_id,
                        parser_name=parse_job.parser_name,
                        parser_version=parse_job.parser_version,
                        parse_status=parse_job.parse_status,
                        parse_attempts=parse_job.parse_attempts,
                        parsed_at=parse_job.parsed_at,
                        page_count=parse_text.page_count if parse_text is not None else None,
                        char_count=parse_text.char_count if parse_text is not None else None,
                        has_text=parse_text is not None,
                        last_parse_error=parse_job.last_parse_error,
                    )
                    for parse_job, parse_text in parse_rows
                ],
                best_parse_job_id=best_parse_job_id,
                snippet=snippet,
            )
        )

    return CompanyTimelineResponse(
        code=normalized_code,
        company_name=company_name,
        total=total,
        limit=limit,
        offset=offset,
        results=results,
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
