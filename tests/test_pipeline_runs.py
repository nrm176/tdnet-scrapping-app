from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.backend.services.pipeline_service import get_pipeline_run_history, list_pipeline_run_history
from tdnet.orm import Base
from tdnet.pipeline_runs import (
    finish_pipeline_run,
    finish_pipeline_step,
    parse_step_metrics,
    skip_pipeline_step,
    start_pipeline_run,
    start_pipeline_step,
)


def test_parse_step_metrics_extracts_cli_summary_lines() -> None:
    metrics = parse_step_metrics(
        """
Candidate files: 12
Parsed files: 10
Skipped files: 1
Failed files: 1
Elapsed seconds: 3.25
Files per second: 3.077
Estimated remaining time: 0s
"""
    )

    assert metrics["candidate_files"] == 12
    assert metrics["parsed_files"] == 10
    assert metrics["failed_files"] == 1
    assert metrics["elapsed_seconds"] == 3.25
    assert metrics["estimated_remaining_time"] == "0s"


@pytest.mark.asyncio
async def test_pipeline_run_history_service_returns_step_summaries() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await start_pipeline_run(
            session,
            run_id="20260607-101010-1234",
            requested_start_date=date(2026, 6, 1),
            effective_start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 7),
            date_count=3,
            checkpoint_latest_date=date(2026, 6, 4),
            checkpoint_start_date=date(2026, 6, 5),
            checkpoint_applied=True,
            options={"days": 7, "retry_failed": False},
            limits={"download": 20000},
            strategies={"ixbrl": "garbled"},
            skip_flags={"parse": False},
            log_path="logs/tdnet-all-in-one-20260607.log",
            latest_log_path="logs/tdnet-all-in-one-latest.log",
        )
        await start_pipeline_step(session, run_id="20260607-101010-1234", step_name="scrape:2026-06-05", step_order=1)
        await finish_pipeline_step(
            session,
            run_id="20260607-101010-1234",
            step_name="scrape:2026-06-05",
            status="completed",
            elapsed_seconds=2.0,
            exit_code=0,
            command="tdnet scrape --date 2026-06-05 --persist",
            metrics={"persisted_disclosures": 39},
        )
        await start_pipeline_step(session, run_id="20260607-101010-1234", step_name="download", step_order=2)
        await finish_pipeline_step(
            session,
            run_id="20260607-101010-1234",
            step_name="download",
            status="failed",
            elapsed_seconds=4.0,
            exit_code=1,
            command="tdnet download --limit 10",
            metrics={"candidate_disclosures": 10, "failed_files": 1},
            error_context="download failed",
        )
        await skip_pipeline_step(
            session,
            run_id="20260607-101010-1234",
            step_name="ocr",
            step_order=3,
            reason="not requested",
        )
        await finish_pipeline_run(
            session,
            run_id="20260607-101010-1234",
            status="failed",
            elapsed_seconds=9.0,
            failed_step="download",
            exit_code=1,
        )

        runs = await list_pipeline_run_history(session)
        detail = await get_pipeline_run_history(session, "20260607-101010-1234")

    assert runs.total == 1
    assert runs.runs[0].run_id == "20260607-101010-1234"
    assert runs.runs[0].status == "failed"
    assert runs.runs[0].step_count == 3
    assert runs.runs[0].completed_steps == 1
    assert runs.runs[0].failed_steps == 1
    assert runs.runs[0].skipped_steps == 1
    assert detail is not None
    assert detail.checkpoint_applied is True
    assert detail.steps[0].step_name == "scrape:2026-06-05"
    assert detail.steps[1].metrics["failed_files"] == 1
    assert detail.steps[1].error_context == "download failed"
    assert detail.steps[2].reason == "not requested"
