"""Pipeline run history API endpoints."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.schemas import PipelineRunDetailResponse, PipelineRunsResponse
from app.backend.services.pipeline_service import get_pipeline_run_history, list_pipeline_run_history
from tdnet.database import get_session

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.get("/pipeline-runs", response_model=PipelineRunsResponse)
async def pipeline_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Literal["running", "completed", "failed"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PipelineRunsResponse:
    return await list_pipeline_run_history(session, status=status, limit=limit, offset=offset)


@router.get("/pipeline-runs/{run_id}", response_model=PipelineRunDetailResponse)
async def pipeline_run_detail(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PipelineRunDetailResponse:
    detail = await get_pipeline_run_history(session, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return detail
