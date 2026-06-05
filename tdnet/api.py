"""FastAPI application for persisted TDnet disclosures."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_session, init_db
from .models import TdnetDisclosure
from .orm import (
    DisclosureFileRecord,
    DisclosureRecord,
    DocumentAnalysisResultRecord,
    DocumentParseJobRecord,
    DocumentParseTextRecord,
    ReportTagAssignmentRecord,
    ReportTagRecord,
)
from .repository import (
    count_disclosure_files,
    count_disclosures,
    disclosure_from_record,
    get_disclosure,
    query_disclosure_files,
    query_disclosures,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


app = FastAPI(title=settings.api_title, version=settings.api_version, lifespan=lifespan)


class HealthResponse(BaseModel):
    status: str
    database: str


class DisclosureListResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    disclosures: list[TdnetDisclosure]


class DisclosureFileResponse(BaseModel):
    id: int
    disclosure_id: str
    file_type: str
    source_url: str
    source_file_id: str
    storage_bucket: str
    storage_path: str
    content_type: str | None
    file_size_bytes: int | None
    sha256: str | None
    download_status: str
    download_attempts: int
    downloaded_at: datetime | None
    last_download_error: str | None


class DisclosureFileListResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    files: list[DisclosureFileResponse]


class ParseTextSummaryResponse(BaseModel):
    id: int
    parse_job_id: int
    page_count: int = Field(ge=0)
    char_count: int = Field(ge=0)
    content_sha256: str
    created_at: datetime
    updated_at: datetime


class ParseJobLineageResponse(BaseModel):
    id: int
    file_id: int
    parser_name: str
    parser_version: str
    parse_status: str
    parse_attempts: int = Field(ge=0)
    text_path: str | None
    text_sha256: str | None
    parsed_at: datetime | None
    last_parse_error: str | None
    parse_text: ParseTextSummaryResponse | None = None


class AnalysisResultResponse(BaseModel):
    id: int
    file_id: int
    parse_job_id: int | None
    analysis_type: str
    analyzer_name: str
    analyzer_version: str
    status: str
    result_json: dict | None
    result_text: str | None
    analyzed_at: datetime | None
    last_analysis_error: str | None
    created_at: datetime
    updated_at: datetime


class ReportTagAssignmentLineageResponse(BaseModel):
    slug: str
    label_ja: str
    label_en: str
    is_primary: bool
    confidence: float = Field(ge=0, le=1)
    source: str
    file_id: int | None
    parse_job_id: int | None
    evidence_json: dict | None
    tagger_name: str
    tagger_version: str


class DisclosureFileLineageResponse(DisclosureFileResponse):
    parse_jobs: list[ParseJobLineageResponse] = Field(default_factory=list)
    analysis_results: list[AnalysisResultResponse] = Field(default_factory=list)


class DisclosureLineageResponse(BaseModel):
    disclosure: TdnetDisclosure
    tags: list[ReportTagAssignmentLineageResponse] = Field(default_factory=list)
    files: list[DisclosureFileLineageResponse] = Field(default_factory=list)


class ParserStatusResponse(BaseModel):
    disclosure_id: str
    files: list[DisclosureFileLineageResponse] = Field(default_factory=list)


class CompanyTimelineDisclosureResponse(BaseModel):
    disclosure: TdnetDisclosure
    tags: list[ReportTagAssignmentLineageResponse] = Field(default_factory=list)
    file_count: int = Field(ge=0)
    completed_file_count: int = Field(ge=0)
    parse_job_count: int = Field(ge=0)
    completed_parse_job_count: int = Field(ge=0)
    analysis_result_count: int = Field(ge=0)
    files: list[DisclosureFileLineageResponse] = Field(default_factory=list)


class CompanyTimelineResponse(BaseModel):
    code: str
    company_name: str | None
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    disclosures: list[CompanyTimelineDisclosureResponse] = Field(default_factory=list)


class ParserQualityStatusCount(BaseModel):
    parse_status: str
    count: int = Field(ge=0)


class ParserQualityParserResponse(BaseModel):
    parser_name: str
    parser_version: str
    total_jobs: int = Field(ge=0)
    completed_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    running_jobs: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)
    parse_texts: int = Field(ge=0)
    average_char_count: float | None


class ParserQualityResponse(BaseModel):
    total_jobs: int = Field(ge=0)
    completed_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    running_jobs: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)
    parse_texts: int = Field(ge=0)
    status_counts: list[ParserQualityStatusCount] = Field(default_factory=list)
    parsers: list[ParserQualityParserResponse] = Field(default_factory=list)


class ReportTagSummaryResponse(BaseModel):
    slug: str
    label_ja: str
    label_en: str
    description: str
    priority: int
    active: bool
    assignment_count: int = Field(ge=0)
    primary_count: int = Field(ge=0)


class ReportTagSummaryListResponse(BaseModel):
    tags: list[ReportTagSummaryResponse] = Field(default_factory=list)


def _parse_text_summary(record: DocumentParseTextRecord | None) -> ParseTextSummaryResponse | None:
    if record is None:
        return None
    return ParseTextSummaryResponse(
        id=record.id,
        parse_job_id=record.parse_job_id,
        page_count=record.page_count,
        char_count=record.char_count,
        content_sha256=record.content_sha256,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _parse_job_lineage(
    record: DocumentParseJobRecord,
    parse_text: DocumentParseTextRecord | None,
) -> ParseJobLineageResponse:
    return ParseJobLineageResponse(
        id=record.id,
        file_id=record.file_id,
        parser_name=record.parser_name,
        parser_version=record.parser_version,
        parse_status=record.parse_status,
        parse_attempts=record.parse_attempts,
        text_path=record.text_path,
        text_sha256=record.text_sha256,
        parsed_at=record.parsed_at,
        last_parse_error=record.last_parse_error,
        parse_text=_parse_text_summary(parse_text),
    )


def _analysis_result_response(record: DocumentAnalysisResultRecord) -> AnalysisResultResponse:
    return AnalysisResultResponse(
        id=record.id,
        file_id=record.file_id,
        parse_job_id=record.parse_job_id,
        analysis_type=record.analysis_type,
        analyzer_name=record.analyzer_name,
        analyzer_version=record.analyzer_version,
        status=record.status,
        result_json=record.result_json,
        result_text=record.result_text,
        analyzed_at=record.analyzed_at,
        last_analysis_error=record.last_analysis_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _tag_assignment_response(
    assignment: ReportTagAssignmentRecord,
    tag: ReportTagRecord,
) -> ReportTagAssignmentLineageResponse:
    return ReportTagAssignmentLineageResponse(
        slug=assignment.tag_slug,
        label_ja=tag.label_ja,
        label_en=tag.label_en,
        is_primary=assignment.is_primary,
        confidence=assignment.confidence,
        source=assignment.source,
        file_id=assignment.file_id,
        parse_job_id=assignment.parse_job_id,
        evidence_json=assignment.evidence_json,
        tagger_name=assignment.tagger_name,
        tagger_version=assignment.tagger_version,
    )


def _file_lineage_response(
    record: DisclosureFileRecord,
    *,
    parse_jobs: list[ParseJobLineageResponse] | None = None,
    analysis_results: list[AnalysisResultResponse] | None = None,
) -> DisclosureFileLineageResponse:
    return DisclosureFileLineageResponse(
        **DisclosureFileResponse.model_validate(record, from_attributes=True).model_dump(),
        parse_jobs=parse_jobs or [],
        analysis_results=analysis_results or [],
    )


def _sum_when(condition) -> object:
    return func.sum(case((condition, 1), else_=0))


async def _load_lineage(
    session: AsyncSession,
    disclosure_records: list[DisclosureRecord],
) -> dict[str, DisclosureLineageResponse]:
    if not disclosure_records:
        return {}

    disclosure_ids = [record.id for record in disclosure_records]
    file_records = list(
        (
            await session.scalars(
                select(DisclosureFileRecord)
                .where(DisclosureFileRecord.disclosure_id.in_(disclosure_ids))
                .order_by(
                    DisclosureFileRecord.disclosure_id.asc(),
                    DisclosureFileRecord.file_type.asc(),
                    DisclosureFileRecord.id.asc(),
                )
            )
        ).all()
    )
    file_ids = [record.id for record in file_records]

    parse_jobs_by_file: dict[int, list[ParseJobLineageResponse]] = defaultdict(list)
    analysis_results_by_file: dict[int, list[AnalysisResultResponse]] = defaultdict(list)
    if file_ids:
        parse_rows = (
            await session.execute(
                select(DocumentParseJobRecord, DocumentParseTextRecord)
                .outerjoin(
                    DocumentParseTextRecord,
                    DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id,
                )
                .where(DocumentParseJobRecord.file_id.in_(file_ids))
                .order_by(
                    DocumentParseJobRecord.file_id.asc(),
                    DocumentParseJobRecord.parser_name.asc(),
                    DocumentParseJobRecord.parser_version.desc(),
                    DocumentParseJobRecord.id.asc(),
                )
            )
        ).all()
        for parse_job, parse_text in parse_rows:
            parse_jobs_by_file[parse_job.file_id].append(_parse_job_lineage(parse_job, parse_text))

        analysis_records = (
            await session.scalars(
                select(DocumentAnalysisResultRecord)
                .where(DocumentAnalysisResultRecord.file_id.in_(file_ids))
                .order_by(
                    DocumentAnalysisResultRecord.file_id.asc(),
                    DocumentAnalysisResultRecord.analysis_type.asc(),
                    DocumentAnalysisResultRecord.id.asc(),
                )
            )
        ).all()
        for analysis_record in analysis_records:
            analysis_results_by_file[analysis_record.file_id].append(_analysis_result_response(analysis_record))

    files_by_disclosure: dict[str, list[DisclosureFileLineageResponse]] = defaultdict(list)
    for file_record in file_records:
        files_by_disclosure[file_record.disclosure_id].append(
            _file_lineage_response(
                file_record,
                parse_jobs=parse_jobs_by_file[file_record.id],
                analysis_results=analysis_results_by_file[file_record.id],
            )
        )

    tag_rows = (
        await session.execute(
            select(ReportTagAssignmentRecord, ReportTagRecord)
            .join(ReportTagRecord, ReportTagRecord.slug == ReportTagAssignmentRecord.tag_slug)
            .where(ReportTagAssignmentRecord.disclosure_id.in_(disclosure_ids))
            .order_by(
                ReportTagAssignmentRecord.disclosure_id.asc(),
                ReportTagAssignmentRecord.is_primary.desc(),
                ReportTagRecord.priority.asc(),
                ReportTagRecord.slug.asc(),
            )
        )
    ).all()
    tags_by_disclosure: dict[str, list[ReportTagAssignmentLineageResponse]] = defaultdict(list)
    for assignment, tag in tag_rows:
        tags_by_disclosure[assignment.disclosure_id].append(_tag_assignment_response(assignment, tag))

    return {
        disclosure_record.id: DisclosureLineageResponse(
            disclosure=disclosure_from_record(disclosure_record),
            tags=tags_by_disclosure[disclosure_record.id],
            files=files_by_disclosure[disclosure_record.id],
        )
        for disclosure_record in disclosure_records
    }


@app.get("/health", response_model=HealthResponse)
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> HealthResponse:
    await session.execute(text("select 1"))
    return HealthResponse(status="ok", database="ok")


@app.get("/disclosures", response_model=DisclosureListResponse)
async def list_disclosures(
    session: Annotated[AsyncSession, Depends(get_session)],
    disclosure_date: Annotated[date | None, Query(alias="date")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    code: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureListResponse:
    total = await count_disclosures(
        session,
        disclosure_date=disclosure_date,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    disclosures = await query_disclosures(
        session,
        disclosure_date=disclosure_date,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        offset=offset,
    )
    return DisclosureListResponse(
        total=total,
        limit=limit,
        offset=offset,
        disclosures=disclosures,
    )


@app.get("/disclosures/{disclosure_id}", response_model=TdnetDisclosure)
async def read_disclosure(
    disclosure_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TdnetDisclosure:
    disclosure = await get_disclosure(session, disclosure_id)
    if disclosure is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    return disclosure


@app.get("/disclosures/{disclosure_id}/lineage", response_model=DisclosureLineageResponse)
async def read_disclosure_lineage(
    disclosure_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DisclosureLineageResponse:
    record = await session.get(DisclosureRecord, disclosure_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    lineage = await _load_lineage(session, [record])
    return lineage[disclosure_id]


@app.get("/disclosures/{disclosure_id}/parser-status", response_model=ParserStatusResponse)
async def read_disclosure_parser_status(
    disclosure_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ParserStatusResponse:
    record = await session.get(DisclosureRecord, disclosure_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    lineage = await _load_lineage(session, [record])
    return ParserStatusResponse(disclosure_id=disclosure_id, files=lineage[disclosure_id].files)


@app.get("/disclosure-files", response_model=DisclosureFileListResponse)
async def list_disclosure_files(
    session: Annotated[AsyncSession, Depends(get_session)],
    disclosure_id: str | None = None,
    download_status: str | None = None,
    file_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureFileListResponse:
    total = await count_disclosure_files(
        session,
        disclosure_id=disclosure_id,
        download_status=download_status,
        file_type=file_type,
    )
    records = await query_disclosure_files(
        session,
        disclosure_id=disclosure_id,
        download_status=download_status,
        file_type=file_type,
        limit=limit,
        offset=offset,
    )
    return DisclosureFileListResponse(
        total=total,
        limit=limit,
        offset=offset,
        files=[DisclosureFileResponse.model_validate(record, from_attributes=True) for record in records],
    )


@app.get("/companies/{code}/disclosures", response_model=DisclosureListResponse)
async def list_company_disclosures(
    code: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureListResponse:
    total = await count_disclosures(
        session,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    disclosures = await query_disclosures(
        session,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        offset=offset,
    )
    return DisclosureListResponse(
        total=total,
        limit=limit,
        offset=offset,
        disclosures=disclosures,
    )


@app.get("/companies/{code}/timeline", response_model=CompanyTimelineResponse)
async def read_company_timeline(
    code: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompanyTimelineResponse:
    normalized_code = code.strip().upper()
    total = await count_disclosures(
        session,
        date_from=date_from,
        date_to=date_to,
        code=normalized_code,
    )
    stmt = select(DisclosureRecord).where(DisclosureRecord.code == normalized_code)
    if date_from is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    stmt = (
        stmt.order_by(
            DisclosureRecord.disclosure_date.desc(),
            DisclosureRecord.time.desc(),
            DisclosureRecord.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    disclosure_records = list(
        (await session.scalars(stmt)).all()
    )
    lineage_by_disclosure = await _load_lineage(session, disclosure_records)
    timeline_items: list[CompanyTimelineDisclosureResponse] = []
    for disclosure_record in disclosure_records:
        lineage = lineage_by_disclosure[disclosure_record.id]
        parse_job_count = sum(len(file.parse_jobs) for file in lineage.files)
        completed_parse_job_count = sum(
            1 for file in lineage.files for parse_job in file.parse_jobs if parse_job.parse_status == "completed"
        )
        analysis_result_count = sum(len(file.analysis_results) for file in lineage.files)
        timeline_items.append(
            CompanyTimelineDisclosureResponse(
                disclosure=lineage.disclosure,
                tags=lineage.tags,
                file_count=len(lineage.files),
                completed_file_count=sum(1 for file in lineage.files if file.download_status == "completed"),
                parse_job_count=parse_job_count,
                completed_parse_job_count=completed_parse_job_count,
                analysis_result_count=analysis_result_count,
                files=lineage.files,
            )
        )
    return CompanyTimelineResponse(
        code=normalized_code,
        company_name=timeline_items[0].disclosure.name if timeline_items else None,
        total=total,
        limit=limit,
        offset=offset,
        disclosures=timeline_items,
    )


@app.get("/parser-quality", response_model=ParserQualityResponse)
async def read_parser_quality(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ParserQualityResponse:
    total_stmt = (
        select(
            func.count(DocumentParseJobRecord.id),
            _sum_when(DocumentParseJobRecord.parse_status == "completed"),
            _sum_when(DocumentParseJobRecord.parse_status == "failed"),
            _sum_when(DocumentParseJobRecord.parse_status == "running"),
            _sum_when(DocumentParseJobRecord.parse_status == "pending"),
            func.count(DocumentParseTextRecord.id),
        )
        .select_from(DocumentParseJobRecord)
        .outerjoin(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
    )
    total_jobs, completed_jobs, failed_jobs, running_jobs, pending_jobs, parse_texts = (
        await session.execute(total_stmt)
    ).one()

    status_rows = (
        await session.execute(
            select(DocumentParseJobRecord.parse_status, func.count(DocumentParseJobRecord.id))
            .group_by(DocumentParseJobRecord.parse_status)
            .order_by(DocumentParseJobRecord.parse_status.asc())
        )
    ).all()
    parser_rows = (
        await session.execute(
            select(
                DocumentParseJobRecord.parser_name,
                DocumentParseJobRecord.parser_version,
                func.count(DocumentParseJobRecord.id),
                _sum_when(DocumentParseJobRecord.parse_status == "completed"),
                _sum_when(DocumentParseJobRecord.parse_status == "failed"),
                _sum_when(DocumentParseJobRecord.parse_status == "running"),
                _sum_when(DocumentParseJobRecord.parse_status == "pending"),
                func.count(DocumentParseTextRecord.id),
                func.avg(DocumentParseTextRecord.char_count),
            )
            .select_from(DocumentParseJobRecord)
            .outerjoin(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
            .group_by(DocumentParseJobRecord.parser_name, DocumentParseJobRecord.parser_version)
            .order_by(DocumentParseJobRecord.parser_name.asc(), DocumentParseJobRecord.parser_version.desc())
        )
    ).all()
    return ParserQualityResponse(
        total_jobs=int(total_jobs or 0),
        completed_jobs=int(completed_jobs or 0),
        failed_jobs=int(failed_jobs or 0),
        running_jobs=int(running_jobs or 0),
        pending_jobs=int(pending_jobs or 0),
        parse_texts=int(parse_texts or 0),
        status_counts=[
            ParserQualityStatusCount(parse_status=parse_status, count=int(count or 0))
            for parse_status, count in status_rows
        ],
        parsers=[
            ParserQualityParserResponse(
                parser_name=parser_name,
                parser_version=parser_version,
                total_jobs=int(total or 0),
                completed_jobs=int(completed or 0),
                failed_jobs=int(failed or 0),
                running_jobs=int(running or 0),
                pending_jobs=int(pending or 0),
                parse_texts=int(texts or 0),
                average_char_count=float(average_chars) if average_chars is not None else None,
            )
            for (
                parser_name,
                parser_version,
                total,
                completed,
                failed,
                running,
                pending,
                texts,
                average_chars,
            ) in parser_rows
        ],
    )


@app.get("/tags", response_model=ReportTagSummaryListResponse)
async def read_tag_summaries(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportTagSummaryListResponse:
    tag_records = (
        await session.scalars(
            select(ReportTagRecord).order_by(ReportTagRecord.priority.asc(), ReportTagRecord.slug.asc())
        )
    ).all()
    count_rows = (
        await session.execute(
            select(
                ReportTagAssignmentRecord.tag_slug,
                func.count(ReportTagAssignmentRecord.id),
                _sum_when(ReportTagAssignmentRecord.is_primary.is_(True)),
            ).group_by(ReportTagAssignmentRecord.tag_slug)
        )
    ).all()
    counts = {
        tag_slug: (int(assignment_count or 0), int(primary_count or 0))
        for tag_slug, assignment_count, primary_count in count_rows
    }
    return ReportTagSummaryListResponse(
        tags=[
            ReportTagSummaryResponse(
                slug=record.slug,
                label_ja=record.label_ja,
                label_en=record.label_en,
                description=record.description,
                priority=record.priority,
                active=record.active,
                assignment_count=counts.get(record.slug, (0, 0))[0],
                primary_count=counts.get(record.slug, (0, 0))[1],
            )
            for record in tag_records
        ]
    )
