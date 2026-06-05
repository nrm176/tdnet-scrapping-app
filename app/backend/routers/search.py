"""Search and review API endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.schemas import (
    CompanyTimelineResponse,
    ParserOptionsResponse,
    ParserQualityResponse,
    ParseJobDetailResponse,
    ParseSearchResponse,
    ReportCalendarResponse,
    ReportTagsResponse,
)
from app.backend.services.review_service import render_parse_job_page
from app.backend.services.search_service import (
    get_company_timeline,
    get_parser_quality,
    get_parse_job_detail,
    list_report_calendar_days,
    list_report_tags,
    list_parser_options,
    search_parse_texts,
)
from tdnet.database import get_session

router = APIRouter(prefix="/api", tags=["review"])


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        year_text, month_text = month.split("-", 1)
        year = int(year_text)
        month_index = int(month_text)
        if month_index < 1 or month_index > 12:
            raise ValueError
        month_start = date(year, month_index, 1)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Month must use YYYY-MM format") from exc

    if month_index == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_index + 1, 1)
    return month_start, next_month - timedelta(days=1)


def _normalize_query_tags(tags: list[str] | None) -> list[str]:
    values: list[str] = []
    for raw_value in tags or []:
        for part in raw_value.split(","):
            value = part.strip().lower()
            if value and value not in values:
                values.append(value)
    return values


@router.get("/parsers", response_model=ParserOptionsResponse)
async def parsers(session: Annotated[AsyncSession, Depends(get_session)]) -> ParserOptionsResponse:
    return ParserOptionsResponse(parsers=await list_parser_options(session))


@router.get("/parser-quality", response_model=ParserQualityResponse)
async def parser_quality(session: Annotated[AsyncSession, Depends(get_session)]) -> ParserQualityResponse:
    return await get_parser_quality(session)


@router.get("/tags", response_model=ReportTagsResponse)
async def tags(session: Annotated[AsyncSession, Depends(get_session)]) -> ReportTagsResponse:
    return ReportTagsResponse(tags=await list_report_tags(session))


@router.get("/calendar", response_model=ReportCalendarResponse)
async def report_calendar(
    session: Annotated[AsyncSession, Depends(get_session)],
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    q: str | None = Query(default=None, max_length=500),
    title_q: str | None = Query(default=None, max_length=500),
    text_q: str | None = Query(default=None, max_length=500),
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    tags: Annotated[list[str] | None, Query()] = None,
    tag_mode: Literal["any", "all"] = "any",
    best_only: bool = True,
) -> ReportCalendarResponse:
    month_start, month_end = _month_bounds(month)
    return ReportCalendarResponse(
        month=month,
        days=await list_report_calendar_days(
            session,
            month_start=month_start,
            month_end=month_end,
            query=q,
            title_query=title_q,
            text_query=text_q,
            parser_name=parser_name,
            parser_version=parser_version,
            code=code,
            tags=_normalize_query_tags(tags),
            tag_mode=tag_mode,
            best_only=best_only,
        ),
    )


@router.get("/companies/{code}/timeline", response_model=CompanyTimelineResponse)
async def company_timeline(
    code: Annotated[str, Path(min_length=1, max_length=16)],
    session: Annotated[AsyncSession, Depends(get_session)],
    title_q: str | None = Query(default=None, max_length=500),
    text_q: str | None = Query(default=None, max_length=500),
    parser_name: str | None = None,
    parser_version: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tags: Annotated[list[str] | None, Query()] = None,
    tag_mode: Literal["any", "all"] = "any",
    best_only: bool = True,
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompanyTimelineResponse:
    return await get_company_timeline(
        session,
        code=code,
        title_query=title_q,
        text_query=text_q,
        parser_name=parser_name,
        parser_version=parser_version,
        date_from=date_from,
        date_to=date_to,
        tags=_normalize_query_tags(tags),
        tag_mode=tag_mode,
        best_only=best_only,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get("/search", response_model=ParseSearchResponse)
async def search(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None, max_length=500),
    title_q: str | None = Query(default=None, max_length=500),
    text_q: str | None = Query(default=None, max_length=500),
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tags: Annotated[list[str] | None, Query()] = None,
    tag_mode: Literal["any", "all"] = "any",
    best_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ParseSearchResponse:
    return await search_parse_texts(
        session,
        query=q,
        title_query=title_q,
        text_query=text_q,
        parser_name=parser_name,
        parser_version=parser_version,
        code=code,
        date_from=date_from,
        date_to=date_to,
        tags=_normalize_query_tags(tags),
        tag_mode=tag_mode,
        best_only=best_only,
        limit=limit,
        offset=offset,
    )


@router.get("/review-queue", response_model=ParseSearchResponse)
async def review_queue(
    session: Annotated[AsyncSession, Depends(get_session)],
    parser_name: str | None = None,
    parser_version: str | None = None,
    tags: Annotated[list[str] | None, Query()] = None,
    tag_mode: Literal["any", "all"] = "any",
    best_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ParseSearchResponse:
    return await search_parse_texts(
        session,
        parser_name=parser_name,
        parser_version=parser_version,
        tags=_normalize_query_tags(tags),
        tag_mode=tag_mode,
        best_only=best_only,
        limit=limit,
        offset=offset,
    )


@router.get("/parse-jobs/{parse_job_id}", response_model=ParseJobDetailResponse)
async def parse_job_detail(
    parse_job_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ParseJobDetailResponse:
    detail = await get_parse_job_detail(session, parse_job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Parse job not found")
    return detail


@router.get("/parse-jobs/{parse_job_id}/page-image")
async def parse_job_page_image(
    parse_job_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
) -> FileResponse:
    image_path = await render_parse_job_page(session, parse_job_id=parse_job_id, page=page)
    if image_path is None:
        raise HTTPException(status_code=404, detail="Page image not available")
    return FileResponse(image_path, media_type="image/png")
