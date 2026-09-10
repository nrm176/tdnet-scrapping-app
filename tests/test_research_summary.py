from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

import tdnet.research_summary as summary
from tdnet.research_summary_parser import parse_summary
from tdnet.research_summary_parser import PARSER_VERSION_V2
from tests.test_research_summary_parser import zipped


DATABASE_URL = "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research"
SOURCE_ID = "https://www.release.tdnet.info/inbs/140120260904000001.pdf"
XBRL_URL = "https://www.release.tdnet.info/inbs/081220260904000001.zip"
CONTENT = zipped()
CONTENT_HASH = hashlib.sha256(CONTENT).hexdigest()
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *unused):
        return False


class Connection:
    def __init__(self, *, extraction=None, children=()):
        self.extraction = extraction
        self.children = list(children)
        self.calls = []
        self.closed = False

    def transaction(self):
        return Transaction()

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "current_database" in sql:
            return {"database": "hft_research", "role": "tdnet_ingest", "version": "180001"}
        if "instance_identity" in sql:
            return {"instance_name": "hft_research", "schema_version": 1}
        if "FROM raw_tdnet.document_artifact AS artifact" in sql:
            return {
                "content": CONTENT, "byte_length": len(CONTENT), "source_url": XBRL_URL,
                "snapshot_hash": "a" * 64, "document_snapshot_hash": "b" * 64,
                "document_payload": {"xbrl_available": True, "xbrl_url": "https://www.release.tdnet.info/inbs/081220260904999999.zip"},
            }
        if "FROM raw_tdnet.summary_extraction" in sql:
            return self.extraction
        return None

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.children if "FROM raw_tdnet.summary_fact" in sql else []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "INSERT 0 1"

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_ingest_reads_exact_artifact_and_atomically_inserts_parent_and_typed_facts():
    connection = Connection()
    with patch.object(summary.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch.object(summary, "_utc_now", return_value=NOW):
        result = await summary.ingest_summary(DATABASE_URL, SOURCE_ID, CONTENT_HASH)

    assert result["selected_fact_count"] == 4
    assert result["extraction_hash"] == hashlib.sha256(
        json.dumps(result["payload"], ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    sql = "\n".join(call[1] for call in connection.calls)
    assert "pg_advisory_xact_lock" in sql
    assert "INSERT INTO raw_tdnet.summary_extraction" in sql
    assert "INSERT INTO raw_tdnet.summary_fact" in sql
    fact_call = next(call for call in connection.calls if "INSERT INTO raw_tdnet.summary_fact" in call[1])
    assert fact_call[2][6] == "11141000000"
    assert connection.closed


@pytest.mark.asyncio
async def test_ingest_preserves_old_artifact_provenance_when_document_metadata_is_newer():
    connection = Connection()
    with patch.object(summary.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        result = await summary.ingest_summary(DATABASE_URL, SOURCE_ID, CONTENT_HASH)
    parent_call = next(call for call in connection.calls if "INSERT INTO raw_tdnet.summary_extraction" in call[1])
    assert parent_call[2][3] == "a" * 64
    assert parent_call[2][4] == XBRL_URL
    assert result["persisted"] is True


@pytest.mark.asyncio
async def test_ingest_v2_uses_explicit_parser_version_and_coexists_with_v1_contract():
    payload = parse_summary(CONTENT)
    payload["parser_version"] = PARSER_VERSION_V2
    connection = Connection()
    with patch.object(summary.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch.object(summary, "parse_summary_v2", return_value=payload) as parser:
        result = await summary.ingest_summary(
            DATABASE_URL, SOURCE_ID, CONTENT_HASH, parser_version=PARSER_VERSION_V2,
        )

    parser.assert_called_once_with(CONTENT)
    assert result["parser_version"] == PARSER_VERSION_V2
    parent_call = next(call for call in connection.calls if "INSERT INTO raw_tdnet.summary_extraction" in call[1])
    assert parent_call[2][2] == PARSER_VERSION_V2
    fact_call = next(call for call in connection.calls if "INSERT INTO raw_tdnet.summary_fact" in call[1])
    assert fact_call[2][2] == PARSER_VERSION_V2


@pytest.mark.asyncio
async def test_invalid_hash_and_source_are_rejected_before_connect():
    with patch.object(summary.asyncpg, "connect", new=AsyncMock()) as connect:
        for source_id, content_hash in (("bad", CONTENT_HASH), (SOURCE_ID, "bad")):
            with pytest.raises(ValueError):
                await summary.ingest_summary(DATABASE_URL, source_id, content_hash)
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_broken_stored_artifact_refuses_before_summary_insert():
    connection = Connection()
    original = connection.fetchrow

    async def broken(sql, *args):
        row = await original(sql, *args)
        if row is not None and "document_artifact AS artifact" in sql:
            return {**row, "byte_length": len(CONTENT) + 1}
        return row

    connection.fetchrow = broken
    with patch.object(summary.asyncpg, "connect", new=AsyncMock(return_value=connection)), pytest.raises(ValueError):
        await summary.ingest_summary(DATABASE_URL, SOURCE_ID, CONTENT_HASH)
    assert not any("INSERT INTO raw_tdnet.summary_" in call[1] for call in connection.calls)


def stored_rows():
    payload = parse_summary(CONTENT)
    extraction_hash = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    extraction = {
        "artifact_kind": "xbrl_zip", "snapshot_hash": "a" * 64, "source_url": XBRL_URL,
        "member_path": payload["member_path"], "extraction_hash": extraction_hash,
        "payload": payload, "extracted_at": NOW, "ready_for_analysis": False,
    }
    children = [{
        "ordinal": fact["ordinal"], "concept_qname": fact["concept_qname"],
        "context_id": fact["context_id"], "unit_id": fact["unit_id"],
        "numeric_value": None if fact["value_decimal"] is None else summary.Decimal(fact["value_decimal"]),
        "is_nil": fact["is_nil"], "payload": fact, "ready_for_analysis": False,
    } for fact in payload["facts"]]
    return extraction, children


@pytest.mark.asyncio
async def test_exact_replay_is_full_no_op_and_preserves_timestamp():
    extraction, children = stored_rows()
    connection = Connection(extraction=extraction, children=children)
    with patch.object(summary.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        result = await summary.ingest_summary(DATABASE_URL, SOURCE_ID, CONTENT_HASH)
    assert result["extracted_at"] == NOW.isoformat()
    assert not any("INSERT INTO raw_tdnet.summary_" in call[1] for call in connection.calls)


@pytest.mark.asyncio
async def test_replay_rejects_corrupt_typed_child_projection():
    extraction, children = stored_rows()
    children[0] = {**children[0], "numeric_value": summary.Decimal("1")}
    connection = Connection(extraction=extraction, children=children)
    with patch.object(summary.asyncpg, "connect", new=AsyncMock(return_value=connection)), pytest.raises(ValueError):
        await summary.ingest_summary(DATABASE_URL, SOURCE_ID, CONTENT_HASH)


def test_cli_sanitizes_errors_and_requires_database(monkeypatch, capsys):
    sentinel = "PRIVATE_SENTINEL"
    monkeypatch.delenv("HFT_RESEARCH_DATABASE_URL", raising=False)
    assert summary.main(["--source-id", sentinel, "--content-hash", sentinel]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": "ValueError"}
    assert sentinel not in captured.err


TEST_DSN = os.environ.get("HFT_DISCLOSURE_TEST_DATABASE_URL")


async def seed_artifact(connection, content=CONTENT):
    digits = str(uuid.uuid4().int)[:20]
    source_id = f"https://www.release.tdnet.info/inbs/{digits}.pdf"
    source_url = f"https://www.release.tdnet.info/inbs/{digits}.zip"
    content_hash = hashlib.sha256(content).hexdigest()
    old_snapshot, new_snapshot = uuid.uuid4().hex * 2, uuid.uuid4().hex * 2
    await connection.execute(
        "INSERT INTO raw_tdnet.list_snapshot VALUES ($1, CURRENT_DATE, '{}'::jsonb, $2, $2)",
        old_snapshot, NOW,
    )
    await connection.execute(
        "INSERT INTO raw_tdnet.list_snapshot VALUES ($1, CURRENT_DATE, '{}'::jsonb, $2, $2)",
        new_snapshot, NOW,
    )
    await connection.execute(
        """INSERT INTO raw_tdnet.document
               (source_id, source_date, title, payload, content_hash, snapshot_hash,
                first_observed_at, last_observed_at, ready_for_analysis)
           VALUES ($1, CURRENT_DATE, 'test', $2::jsonb, $3, $4, $5, $5, FALSE)""",
        source_id, json.dumps({"pdf_url": source_id, "xbrl_available": True,
                               "xbrl_url": source_url.replace(".zip", "9.zip")}),
        hashlib.sha256(source_id.encode()).hexdigest(), new_snapshot, NOW,
    )
    await connection.execute(
        """INSERT INTO raw_tdnet.document_artifact
               (source_id, artifact_kind, content_hash, content, byte_length, source_url,
                snapshot_hash, first_observed_at, last_observed_at, ready_for_analysis)
           VALUES ($1, 'xbrl_zip', $2, $3, $4, $5, $6, $7, $7, FALSE)""",
        source_id, content_hash, content, len(content), source_url, old_snapshot, NOW,
    )
    return source_id, source_url, content_hash


@pytest.mark.skipif(not TEST_DSN, reason="requires controller disposable PostgreSQL DSN")
@pytest.mark.asyncio
async def test_pg18_typed_replay_version_coexistence_and_constraints():
    connection = await summary.asyncpg.connect(TEST_DSN)
    try:
        source_id, _, content_hash = await seed_artifact(connection)
        first = await summary.ingest_summary(TEST_DSN, source_id, content_hash)
        again = await summary.ingest_summary(TEST_DSN, source_id, content_hash)
        assert first["extracted_at"] == again["extracted_at"]
        row = await connection.fetchrow(
            """SELECT numeric_value, payload, ready_for_analysis FROM raw_tdnet.summary_fact
               WHERE source_id=$1 AND content_hash=$2 AND parser_version=$3 AND ordinal=1""",
            source_id, content_hash, summary.PARSER_VERSION,
        )
        assert row["numeric_value"] == summary.Decimal("11141000000")
        assert summary._json(row["payload"])["value_decimal"] == "11141000000"
        assert row["ready_for_analysis"] is False
        await connection.execute(
            """INSERT INTO raw_tdnet.summary_extraction
                   (source_id, content_hash, parser_version, snapshot_hash, source_url, member_path,
                    extraction_hash, payload, extracted_at, ready_for_analysis)
               SELECT source_id, content_hash, 'other-parser-version', snapshot_hash, source_url,
                      member_path, $3, '{}'::jsonb, extracted_at, FALSE
               FROM raw_tdnet.summary_extraction
               WHERE source_id=$1 AND content_hash=$2 AND parser_version=$4""",
            source_id, content_hash, "f" * 64, summary.PARSER_VERSION,
        )
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.summary_extraction WHERE source_id=$1 AND content_hash=$2",
            source_id, content_hash,
        ) == 2
        with pytest.raises(summary.asyncpg.CheckViolationError):
            async with connection.transaction():
                await connection.execute(
                    "UPDATE raw_tdnet.summary_extraction SET ready_for_analysis=TRUE WHERE source_id=$1",
                    source_id,
                )
        with pytest.raises(summary.asyncpg.ForeignKeyViolationError):
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO raw_tdnet.summary_fact
                           (source_id, content_hash, parser_version, ordinal, concept_qname,
                            context_id, unit_id, numeric_value, is_nil, payload, ready_for_analysis)
                       VALUES ($1, $2, 'missing', 1, 'x', 'c', 'u', 1, FALSE, '{}'::jsonb, FALSE)""",
                    source_id, content_hash,
                )
    finally:
        await connection.close()


@pytest.mark.skipif(not TEST_DSN, reason="requires controller disposable PostgreSQL DSN")
@pytest.mark.asyncio
async def test_pg18_mid_write_failure_rolls_back_and_corrupt_artifact_persists_nothing():
    connection = await summary.asyncpg.connect(TEST_DSN)
    try:
        source_id, _, content_hash = await seed_artifact(connection)
        invalid = parse_summary(CONTENT)
        invalid["facts"][1]["ordinal"] = 1
        with patch.object(summary, "parse_summary", return_value=invalid), pytest.raises(
            summary.asyncpg.UniqueViolationError
        ):
            await summary.ingest_summary(TEST_DSN, source_id, content_hash)
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.summary_extraction WHERE source_id=$1 AND content_hash=$2",
            source_id, content_hash,
        ) == 0
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.summary_fact WHERE source_id=$1 AND content_hash=$2",
            source_id, content_hash,
        ) == 0

        broken_id, _, broken_hash = await seed_artifact(connection)
        other_content = zipped().replace(b"PK", b"PX", 1)
        await connection.execute(
            """UPDATE raw_tdnet.document_artifact SET content=$3, byte_length=$4
               WHERE source_id=$1 AND content_hash=$2""",
            broken_id, broken_hash, other_content, len(other_content),
        )
        with pytest.raises(ValueError):
            await summary.ingest_summary(TEST_DSN, broken_id, broken_hash)
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.summary_extraction WHERE source_id=$1", broken_id,
        ) == 0
    finally:
        await connection.close()
