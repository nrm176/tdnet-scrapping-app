"""Structured runtime statistics for long-running TDnet jobs."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@dataclass(frozen=True)
class JobStatsSnapshot:
    elapsed_seconds: float
    processed: int
    succeeded: int
    failed: int
    skipped: int
    total_items: int
    scheduled_items: int
    workers: int
    items_per_second: float
    average_item_seconds: float
    median_item_seconds: float
    estimated_total_seconds: float | None
    estimated_remaining_seconds: float | None


@dataclass
class JobStatsLogger:
    """Log throughput and ETA snapshots for a bounded processing job."""

    job_name: str
    logger: logging.Logger
    total_items: int
    scheduled_items: int
    workers: int = 1
    progress_interval: int = 10
    clock: Callable[[], float] = time.perf_counter
    started_at: float = field(init=False)
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    item_seconds: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.started_at = self.clock()
        self.progress_interval = max(1, self.progress_interval)

    @property
    def processed(self) -> int:
        return self.succeeded + self.failed + self.skipped

    def snapshot(self) -> JobStatsSnapshot:
        elapsed = max(0.0, self.clock() - self.started_at)
        rate = self.processed / elapsed if elapsed > 0 and self.processed else 0.0
        estimated_total = self.total_items / rate if rate > 0 else None
        remaining = max(0, self.total_items - self.processed)
        estimated_remaining = remaining / rate if rate > 0 else None
        return JobStatsSnapshot(
            elapsed_seconds=elapsed,
            processed=self.processed,
            succeeded=self.succeeded,
            failed=self.failed,
            skipped=self.skipped,
            total_items=self.total_items,
            scheduled_items=self.scheduled_items,
            workers=self.workers,
            items_per_second=rate,
            average_item_seconds=mean(self.item_seconds) if self.item_seconds else 0.0,
            median_item_seconds=median(self.item_seconds) if self.item_seconds else 0.0,
            estimated_total_seconds=estimated_total,
            estimated_remaining_seconds=estimated_remaining,
        )

    def log_start(self, **fields: Any) -> None:
        self.logger.info(
            "%s_start total_items=%s scheduled_items=%s workers=%s %s",
            self.job_name,
            self.total_items,
            self.scheduled_items,
            self.workers,
            _format_fields(fields),
        )

    def record_success(self, *, item_id: int | str, item_seconds: float, **fields: Any) -> None:
        self.succeeded += 1
        self.item_seconds.append(item_seconds)
        self.logger.info(
            "%s_item_completed item_id=%s item_seconds=%.3f %s",
            self.job_name,
            item_id,
            item_seconds,
            _format_fields(fields),
        )
        self.log_progress_if_due()

    def record_failure(self, *, item_id: int | str, item_seconds: float, error: str, **fields: Any) -> None:
        self.failed += 1
        self.item_seconds.append(item_seconds)
        self.logger.warning(
            "%s_item_failed item_id=%s item_seconds=%.3f error=%s %s",
            self.job_name,
            item_id,
            item_seconds,
            error,
            _format_fields(fields),
        )
        self.log_progress_if_due()

    def record_skipped(self, *, item_id: int | str, **fields: Any) -> None:
        self.skipped += 1
        self.logger.info(
            "%s_item_skipped item_id=%s %s",
            self.job_name,
            item_id,
            _format_fields(fields),
        )
        self.log_progress_if_due()

    def log_progress_if_due(self) -> None:
        if self.processed % self.progress_interval == 0:
            self.log_progress()

    def log_progress(self) -> None:
        snapshot = self.snapshot()
        self.logger.info(
            "%s_progress processed=%s succeeded=%s failed=%s skipped=%s total_items=%s "
            "scheduled_items=%s workers=%s elapsed_seconds=%.3f items_per_second=%.4f "
            "average_item_seconds=%.3f median_item_seconds=%.3f estimated_total=%s "
            "estimated_remaining=%s",
            self.job_name,
            snapshot.processed,
            snapshot.succeeded,
            snapshot.failed,
            snapshot.skipped,
            snapshot.total_items,
            snapshot.scheduled_items,
            snapshot.workers,
            snapshot.elapsed_seconds,
            snapshot.items_per_second,
            snapshot.average_item_seconds,
            snapshot.median_item_seconds,
            format_seconds(snapshot.estimated_total_seconds),
            format_seconds(snapshot.estimated_remaining_seconds),
        )

    def log_finish(self) -> JobStatsSnapshot:
        snapshot = self.snapshot()
        self.logger.info(
            "%s_finished processed=%s succeeded=%s failed=%s skipped=%s total_items=%s "
            "scheduled_items=%s workers=%s elapsed_seconds=%.3f items_per_second=%.4f "
            "average_item_seconds=%.3f median_item_seconds=%.3f estimated_total=%s "
            "estimated_remaining=%s",
            self.job_name,
            snapshot.processed,
            snapshot.succeeded,
            snapshot.failed,
            snapshot.skipped,
            snapshot.total_items,
            snapshot.scheduled_items,
            snapshot.workers,
            snapshot.elapsed_seconds,
            snapshot.items_per_second,
            snapshot.average_item_seconds,
            snapshot.median_item_seconds,
            format_seconds(snapshot.estimated_total_seconds),
            format_seconds(snapshot.estimated_remaining_seconds),
        )
        return snapshot


def _format_fields(fields: dict[str, Any]) -> str:
    if not fields:
        return ""
    return " ".join(f"{key}={value}" for key, value in fields.items())
