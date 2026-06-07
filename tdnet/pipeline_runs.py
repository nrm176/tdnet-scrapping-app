"""Persistence helpers for TDnet all-in-one pipeline run history."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .orm import PipelineRunRecord, PipelineRunStepRecord

PIPELINE_RUN_STATUSES = {"running", "completed", "failed"}
PIPELINE_STEP_STATUSES = {"running", "completed", "failed", "skipped"}
_METRIC_LINE_PATTERN = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /_()-]{0,80}):\s+(.+?)\s*$")
_METRIC_KEY_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class PipelineRunStepCounts:
    total: int
    completed: int
    failed: int
    skipped: int


@dataclass(frozen=True)
class PipelineRunSummary:
    record: PipelineRunRecord
    step_counts: PipelineRunStepCounts


@dataclass(frozen=True)
class PipelineRunList:
    total: int
    limit: int
    offset: int
    runs: list[PipelineRunSummary]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_status(value: str, allowed: set[str], *, label: str) -> str:
    if value not in allowed:
        raise ValueError(f"Unsupported {label}: {value}")
    return value


def _normalize_metric_key(value: str) -> str:
    normalized = _METRIC_KEY_PATTERN.sub("_", value.strip().lower()).strip("_")
    return normalized or "metric"


def _coerce_metric_value(value: str) -> int | float | str:
    clean_value = value.strip().replace(",", "")
    if re.fullmatch(r"[-+]?\d+", clean_value):
        return int(clean_value)
    if re.fullmatch(r"[-+]?\d+\.\d+", clean_value):
        return float(clean_value)
    return value.strip()


def parse_step_metrics(text: str) -> dict[str, int | float | str]:
    """Extract simple ``Label: value`` summary lines from CLI output."""
    metrics: dict[str, int | float | str] = {}
    for line in text.splitlines():
        match = _METRIC_LINE_PATTERN.match(line)
        if match is None:
            continue
        key = _normalize_metric_key(match.group(1))
        metrics[key] = _coerce_metric_value(match.group(2))
    return metrics


def parse_step_metrics_file(path: str | None) -> dict[str, int | float | str]:
    if not path:
        return {}
    metrics_path = Path(path)
    if not metrics_path.exists():
        return {}
    return parse_step_metrics(metrics_path.read_text(encoding="utf-8", errors="replace"))


def error_context_from_file(path: str | None, *, max_lines: int = 20, max_chars: int = 4000) -> str | None:
    if not path:
        return None
    metrics_path = Path(path)
    if not metrics_path.exists():
        return None
    lines = [line.strip() for line in metrics_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    context = "\n".join(line for line in lines if line)[-max_chars:]
    if not context:
        return None
    return "\n".join(context.splitlines()[-max_lines:])


async def start_pipeline_run(
    session: AsyncSession,
    *,
    run_id: str,
    log_path: str,
    latest_log_path: str | None = None,
    requested_start_date: date | None = None,
    effective_start_date: date | None = None,
    end_date: date | None = None,
    date_count: int = 0,
    checkpoint_latest_date: date | None = None,
    checkpoint_start_date: date | None = None,
    checkpoint_applied: bool = False,
    checkpoint_disabled_reason: str | None = None,
    options: dict[str, Any] | None = None,
    limits: dict[str, Any] | None = None,
    strategies: dict[str, Any] | None = None,
    skip_flags: dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> PipelineRunRecord:
    record = await session.get(PipelineRunRecord, run_id)
    now = started_at or _now()
    values = {
        "status": "running",
        "requested_start_date": requested_start_date,
        "effective_start_date": effective_start_date,
        "end_date": end_date,
        "date_count": max(0, date_count),
        "checkpoint_latest_date": checkpoint_latest_date,
        "checkpoint_start_date": checkpoint_start_date,
        "checkpoint_applied": checkpoint_applied,
        "checkpoint_disabled_reason": checkpoint_disabled_reason,
        "options_json": options or {},
        "limits_json": limits or {},
        "strategies_json": strategies or {},
        "skip_flags_json": skip_flags or {},
        "log_path": log_path,
        "latest_log_path": latest_log_path,
        "started_at": now,
        "finished_at": None,
        "elapsed_seconds": None,
        "failed_step": None,
        "exit_code": None,
        "last_error": None,
    }
    if record is None:
        record = PipelineRunRecord(run_id=run_id, **values)
        session.add(record)
    else:
        for key, value in values.items():
            setattr(record, key, value)
    await session.commit()
    await session.refresh(record)
    return record


async def finish_pipeline_run(
    session: AsyncSession,
    *,
    run_id: str,
    status: str,
    elapsed_seconds: float | None = None,
    failed_step: str | None = None,
    exit_code: int | None = None,
    last_error: str | None = None,
    finished_at: datetime | None = None,
) -> PipelineRunRecord:
    status = _validate_status(status, PIPELINE_RUN_STATUSES, label="pipeline run status")
    record = await session.get(PipelineRunRecord, run_id)
    if record is None:
        raise ValueError(f"Pipeline run does not exist: {run_id}")
    record.status = status
    record.elapsed_seconds = elapsed_seconds
    record.failed_step = failed_step
    record.exit_code = exit_code
    record.last_error = last_error
    record.finished_at = finished_at or _now()
    await session.commit()
    await session.refresh(record)
    return record


async def start_pipeline_step(
    session: AsyncSession,
    *,
    run_id: str,
    step_name: str,
    step_order: int,
    started_at: datetime | None = None,
) -> PipelineRunStepRecord:
    stmt = select(PipelineRunStepRecord).where(
        PipelineRunStepRecord.run_id == run_id,
        PipelineRunStepRecord.step_name == step_name,
    )
    record = await session.scalar(stmt)
    now = started_at or _now()
    values = {
        "step_order": step_order,
        "status": "running",
        "command": None,
        "reason": None,
        "metrics_json": {},
        "error_context": None,
        "exit_code": None,
        "started_at": now,
        "finished_at": None,
        "elapsed_seconds": None,
    }
    if record is None:
        record = PipelineRunStepRecord(run_id=run_id, step_name=step_name, **values)
        session.add(record)
    else:
        for key, value in values.items():
            setattr(record, key, value)
    await session.commit()
    await session.refresh(record)
    return record


async def finish_pipeline_step(
    session: AsyncSession,
    *,
    run_id: str,
    step_name: str,
    status: str,
    elapsed_seconds: float | None = None,
    exit_code: int | None = None,
    command: str | None = None,
    metrics: dict[str, Any] | None = None,
    error_context: str | None = None,
    finished_at: datetime | None = None,
) -> PipelineRunStepRecord:
    status = _validate_status(status, PIPELINE_STEP_STATUSES, label="pipeline step status")
    stmt = select(PipelineRunStepRecord).where(
        PipelineRunStepRecord.run_id == run_id,
        PipelineRunStepRecord.step_name == step_name,
    )
    record = await session.scalar(stmt)
    if record is None:
        raise ValueError(f"Pipeline step does not exist: {run_id}/{step_name}")
    record.status = status
    record.command = command
    record.metrics_json = metrics or {}
    record.error_context = error_context
    record.exit_code = exit_code
    record.elapsed_seconds = elapsed_seconds
    record.finished_at = finished_at or _now()
    await session.commit()
    await session.refresh(record)
    return record


async def skip_pipeline_step(
    session: AsyncSession,
    *,
    run_id: str,
    step_name: str,
    step_order: int,
    reason: str,
    skipped_at: datetime | None = None,
) -> PipelineRunStepRecord:
    stmt = select(PipelineRunStepRecord).where(
        PipelineRunStepRecord.run_id == run_id,
        PipelineRunStepRecord.step_name == step_name,
    )
    record = await session.scalar(stmt)
    now = skipped_at or _now()
    values = {
        "step_order": step_order,
        "status": "skipped",
        "command": None,
        "reason": reason,
        "metrics_json": {},
        "error_context": None,
        "exit_code": None,
        "started_at": now,
        "finished_at": now,
        "elapsed_seconds": 0.0,
    }
    if record is None:
        record = PipelineRunStepRecord(run_id=run_id, step_name=step_name, **values)
        session.add(record)
    else:
        for key, value in values.items():
            setattr(record, key, value)
    await session.commit()
    await session.refresh(record)
    return record


def _pipeline_run_filters(status: str | None = None) -> list:
    filters = []
    if status is not None:
        filters.append(PipelineRunRecord.status == status)
    return filters


async def list_pipeline_runs(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> PipelineRunList:
    if status is not None:
        _validate_status(status, PIPELINE_RUN_STATUSES, label="pipeline run status")
    filters = _pipeline_run_filters(status)
    count_stmt = select(func.count()).select_from(PipelineRunRecord)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = int(await session.scalar(count_stmt) or 0)
    stmt: Select[tuple[PipelineRunRecord]] = (
        select(PipelineRunRecord)
        .where(*filters)
        .order_by(PipelineRunRecord.started_at.desc(), PipelineRunRecord.run_id.desc())
        .limit(limit)
        .offset(offset)
    )
    records = list((await session.scalars(stmt)).all())
    counts = await _load_step_counts(session, [record.run_id for record in records])
    return PipelineRunList(
        total=total,
        limit=limit,
        offset=offset,
        runs=[PipelineRunSummary(record=record, step_counts=counts.get(record.run_id, _empty_step_counts())) for record in records],
    )


async def get_pipeline_run(
    session: AsyncSession,
    run_id: str,
) -> PipelineRunRecord | None:
    stmt = (
        select(PipelineRunRecord)
        .options(selectinload(PipelineRunRecord.steps))
        .where(PipelineRunRecord.run_id == run_id)
    )
    return await session.scalar(stmt)


async def _load_step_counts(
    session: AsyncSession,
    run_ids: Sequence[str],
) -> dict[str, PipelineRunStepCounts]:
    if not run_ids:
        return {}
    stmt = (
        select(PipelineRunStepRecord.run_id, PipelineRunStepRecord.status, func.count())
        .where(PipelineRunStepRecord.run_id.in_(run_ids))
        .group_by(PipelineRunStepRecord.run_id, PipelineRunStepRecord.status)
    )
    rows = (await session.execute(stmt)).all()
    values: dict[str, dict[str, int]] = {run_id: {} for run_id in run_ids}
    for run_id, status, count in rows:
        values.setdefault(run_id, {})[status] = int(count)
    return {
        run_id: PipelineRunStepCounts(
            total=sum(status_counts.values()),
            completed=status_counts.get("completed", 0),
            failed=status_counts.get("failed", 0),
            skipped=status_counts.get("skipped", 0),
        )
        for run_id, status_counts in values.items()
    }


def _empty_step_counts() -> PipelineRunStepCounts:
    return PipelineRunStepCounts(total=0, completed=0, failed=0, skipped=0)
