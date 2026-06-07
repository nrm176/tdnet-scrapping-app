"""Read-only pipeline run history queries for the review app."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.schemas import (
    PipelineRunDetailResponse,
    PipelineRunsResponse,
    PipelineRunStepResponse,
    PipelineRunSummaryResponse,
)
from tdnet.orm import PipelineRunRecord, PipelineRunStepRecord
from tdnet.pipeline_runs import PipelineRunStepCounts, get_pipeline_run, list_pipeline_runs


def _run_summary_to_response(
    record: PipelineRunRecord,
    counts: PipelineRunStepCounts,
) -> PipelineRunSummaryResponse:
    return PipelineRunSummaryResponse(
        run_id=record.run_id,
        status=record.status,
        requested_start_date=record.requested_start_date,
        effective_start_date=record.effective_start_date,
        end_date=record.end_date,
        date_count=record.date_count,
        checkpoint_applied=record.checkpoint_applied,
        checkpoint_disabled_reason=record.checkpoint_disabled_reason,
        options=record.options_json or {},
        limits=record.limits_json or {},
        strategies=record.strategies_json or {},
        skip_flags=record.skip_flags_json or {},
        log_path=record.log_path,
        latest_log_path=record.latest_log_path,
        started_at=record.started_at,
        finished_at=record.finished_at,
        elapsed_seconds=record.elapsed_seconds,
        failed_step=record.failed_step,
        exit_code=record.exit_code,
        last_error=record.last_error,
        step_count=counts.total,
        completed_steps=counts.completed,
        failed_steps=counts.failed,
        skipped_steps=counts.skipped,
    )


def _step_to_response(record: PipelineRunStepRecord) -> PipelineRunStepResponse:
    return PipelineRunStepResponse(
        id=record.id,
        run_id=record.run_id,
        step_name=record.step_name,
        step_order=record.step_order,
        status=record.status,
        command=record.command,
        reason=record.reason,
        metrics=record.metrics_json or {},
        error_context=record.error_context,
        exit_code=record.exit_code,
        started_at=record.started_at,
        finished_at=record.finished_at,
        elapsed_seconds=record.elapsed_seconds,
    )


async def list_pipeline_run_history(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> PipelineRunsResponse:
    runs = await list_pipeline_runs(session, status=status, limit=limit, offset=offset)
    return PipelineRunsResponse(
        total=runs.total,
        limit=runs.limit,
        offset=runs.offset,
        runs=[_run_summary_to_response(item.record, item.step_counts) for item in runs.runs],
    )


async def get_pipeline_run_history(
    session: AsyncSession,
    run_id: str,
) -> PipelineRunDetailResponse | None:
    record = await get_pipeline_run(session, run_id)
    if record is None:
        return None
    steps = sorted(record.steps, key=lambda step: (step.step_order, step.id))
    counts = PipelineRunStepCounts(
        total=len(steps),
        completed=sum(1 for step in steps if step.status == "completed"),
        failed=sum(1 for step in steps if step.status == "failed"),
        skipped=sum(1 for step in steps if step.status == "skipped"),
    )
    summary = _run_summary_to_response(record, counts)
    return PipelineRunDetailResponse(
        **summary.model_dump(),
        checkpoint_latest_date=record.checkpoint_latest_date,
        checkpoint_start_date=record.checkpoint_start_date,
        steps=[_step_to_response(step) for step in steps],
    )
