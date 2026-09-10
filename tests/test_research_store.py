import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import tdnet.research_store as store
from tdnet.research_ingest import snapshot_hash
from tests.test_research_ingest import OBSERVED, capture, html


class Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self, *unused): return False


class Connection:
    def __init__(self, existing=(), marker=None):
        self.existing = list(existing)
        self.calls = []
        self.closed = False
        self.marker = marker or {"instance_name": "hft_research", "schema_version": 1}
    def transaction(self): return Transaction()
    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "current_database" in sql:
            return {"database": "hft_research", "role": "tdnet_ingest", "version": "180001"}
        if "instance_identity" in sql:
            return self.marker
        return None
    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.existing
    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"
    async def close(self): self.closed = True


@pytest.mark.asyncio
async def test_store_validates_all_rows_before_connect_and_rejects_duplicate_conflict():
    bad = capture(pages=[{"url": capture()["raw_payload"]["pages"][0]["url"],
                          "html": html() + html(title="different")}])
    with patch.object(store.asyncpg, "connect", new=AsyncMock()) as connect:
        with pytest.raises(ValueError):
            await store.store_capture(bad, "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research")
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_uses_one_transaction_snapshot_upsert_and_current_upsert():
    connection = Connection()
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch("tdnet.research_ingest._utc_now", return_value=OBSERVED):
        result = await store.store_capture(
            capture(), "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research",
        )
    assert result["record_count"] == 1 and result["persisted"] is True
    sql = "\n".join(call[1] for call in connection.calls)
    assert "pg_advisory_xact_lock" in sql
    assert "ON CONFLICT (snapshot_hash) DO UPDATE" in sql
    assert "LEAST(raw_tdnet.list_snapshot.first_observed_at" in sql
    assert "ON CONFLICT (source_id) DO UPDATE" in sql
    assert "EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at" in sql
    assert connection.closed


@pytest.mark.parametrize("url", [
    "postgresql://wrong:x@127.0.0.1:54790/hft_research",
    "postgresql://tdnet_ingest:x@example.com:54790/hft_research",
    "postgresql://tdnet_ingest:x@127.0.0.1:5432/hft_research",
    "postgresql://tdnet_ingest:x@127.0.0.1:54790/other",
])
def test_database_url_guard_rejects_wrong_role_endpoint_or_database(url):
    with pytest.raises(ValueError):
        store.validate_database_url(url)


@pytest.mark.asyncio
async def test_equal_timestamp_different_content_rejects_inside_transaction():
    existing = [{"source_id": "https://www.release.tdnet.info/inbs/140120260904000001.pdf",
                 "content_hash": "0" * 64, "last_observed_at": OBSERVED}]
    connection = Connection(existing)
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch("tdnet.research_ingest._utc_now", return_value=OBSERVED), pytest.raises(ValueError):
        await store.store_capture(capture(), "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research")
    assert connection.closed


@pytest.mark.asyncio
async def test_store_rejects_missing_or_wrong_instance_marker_before_transaction():
    for marker in ({"instance_name": "other", "schema_version": 1},
                   {"instance_name": "hft_research", "schema_version": 2}):
        connection = Connection(marker=marker)
        with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
             patch("tdnet.research_ingest._utc_now", return_value=OBSERVED), pytest.raises(ValueError):
            await store.store_capture(capture(), "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research")
        assert not any(call[0] == "execute" for call in connection.calls)
        assert connection.closed


TEST_DSN = os.environ.get("HFT_DISCLOSURE_TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DSN, reason="requires controller disposable PostgreSQL DSN")
@pytest.mark.asyncio
async def test_pg18_replay_correction_stale_and_atomic_conflict():
    now = datetime.now(timezone.utc)
    first_seen, corrected_seen = now - timedelta(minutes=2), now - timedelta(minutes=1)
    filename = f"{uuid.uuid4().hex}.pdf"
    first = capture(html(title="初回", href=filename), observed=first_seen.isoformat())
    corrected = capture(html(title="訂正版", href=filename), observed=corrected_seen.isoformat())
    conflicting = capture(html(title="同時刻競合", href=filename), observed=corrected_seen.isoformat())
    source_id = f"https://www.release.tdnet.info/inbs/{filename}"

    a = await store.store_capture(first, TEST_DSN)
    again = await store.store_capture(first, TEST_DSN)
    assert a["snapshot_hash"] == again["snapshot_hash"]
    assert a["record_count"] == 1
    await store.store_capture(corrected, TEST_DSN)
    await store.store_capture(first, TEST_DSN)

    connection = await store.asyncpg.connect(TEST_DSN)
    try:
        row = await connection.fetchrow(
            "SELECT title, first_observed_at, last_observed_at, ready_for_analysis "
            "FROM raw_tdnet.document WHERE source_id = $1", source_id,
        )
        assert dict(row) == {
            "title": "訂正版", "first_observed_at": first_seen,
            "last_observed_at": corrected_seen, "ready_for_analysis": False,
        }
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.document WHERE source_id = $1", source_id,
        ) == 1
    finally:
        await connection.close()

    with pytest.raises(ValueError):
        await store.store_capture(conflicting, TEST_DSN)
    conflict_hash = snapshot_hash(conflicting)
    connection = await store.asyncpg.connect(TEST_DSN)
    try:
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.list_snapshot WHERE snapshot_hash = $1", conflict_hash,
        ) == 0
        assert await connection.fetchval(
            "SELECT title FROM raw_tdnet.document WHERE source_id = $1", source_id,
        ) == "訂正版"
    finally:
        await connection.close()
