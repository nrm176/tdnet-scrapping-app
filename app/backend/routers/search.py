"""Search and review API endpoints."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.schemas import (
    ParserOptionsResponse,
    ParseJobDetailResponse,
    ParseSearchResponse,
)
from app.backend.services.review_service import render_parse_job_page
from app.backend.services.search_service import (
    get_parse_job_detail,
    list_parser_options,
    search_parse_texts,
)
from tdnet.database import get_session

router = APIRouter(prefix="/api", tags=["review"])


@router.get("/parsers", response_model=ParserOptionsResponse)
async def parsers(session: Annotated[AsyncSession, Depends(get_session)]) -> ParserOptionsResponse:
    return ParserOptionsResponse(parsers=await list_parser_options(session))


@router.get("/search", response_model=ParseSearchResponse)
async def search(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None, max_length=500),
    parser_name: str | None = None,
    parser_version: str | None = None,
    code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ParseSearchResponse:
    return await search_parse_texts(
        session,
        query=q,
        parser_name=parser_name,
        parser_version=parser_version,
        code=code,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/review-queue", response_model=ParseSearchResponse)
async def review_queue(
    session: Annotated[AsyncSession, Depends(get_session)],
    parser_name: str | None = None,
    parser_version: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ParseSearchResponse:
    return await search_parse_texts(
        session,
        parser_name=parser_name,
        parser_version=parser_version,
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
