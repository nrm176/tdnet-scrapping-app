from __future__ import annotations

import logging

from tdnet.stats_logging import JobStatsLogger, format_seconds


def test_format_seconds():
    assert format_seconds(None) == "unknown"
    assert format_seconds(4.2) == "4s"
    assert format_seconds(65) == "1m 05s"
    assert format_seconds(3661) == "1h 01m 01s"


def test_job_stats_logger_estimates_remaining_time():
    current = 0.0

    def clock() -> float:
        return current

    stats = JobStatsLogger(
        job_name="parse",
        logger=logging.getLogger("test.stats"),
        total_items=100,
        scheduled_items=10,
        workers=16,
        clock=clock,
        progress_interval=100,
    )

    current = 10.0
    stats.record_success(item_id=1, item_seconds=2.0)
    stats.record_success(item_id=2, item_seconds=3.0)
    snapshot = stats.snapshot()

    assert snapshot.processed == 2
    assert snapshot.items_per_second == 0.2
    assert snapshot.average_item_seconds == 2.5
    assert snapshot.median_item_seconds == 2.5
    assert snapshot.estimated_total_seconds == 500.0
    assert snapshot.estimated_remaining_seconds == 490.0
