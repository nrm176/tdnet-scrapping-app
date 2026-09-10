"""Transactional asyncpg storage for validated TDnet captures."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

import asyncpg


_TEST_DATABASE = re.compile(r"^hft_disclosure_test_[0-9a-f]+$")


def validate_database_url(database_url: str) -> str:
    """Return the validated database name for the one supported local endpoint."""
    try:
        parsed = urlsplit(database_url)
        username = unquote(parsed.username or "")
        database = parsed.path.removeprefix("/")
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid research database URL") from exc
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or username != "tdnet_ingest"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port != 54790
        or not parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("research database URL is outside the approved endpoint")
    if database == "hft_research":
        return database
    test_url = os.environ.get("HFT_DISCLOSURE_TEST_DATABASE_URL")
    if not (_TEST_DATABASE.fullmatch(database) and test_url == database_url):
        raise ValueError("research database name is not approved")
    return database


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


async def store_capture(capture: dict[str, object], database_url: str) -> dict[str, object]:
    """Atomically upsert a validated capture and its current disclosure rows."""
    from tdnet.research_ingest import parse_capture, snapshot_hash

    rows = parse_capture(capture)
    expected_database = validate_database_url(database_url)
    observed_at = datetime.fromisoformat(str(capture["observed_at"])).astimezone(timezone.utc)
    target_date = datetime.fromisoformat(str(capture["target_date"])).date()
    capture_hash = snapshot_hash(capture)
    raw_payload_json = _canonical(capture["raw_payload"])
    row_hashes = {str(row["source_id"]): _sha256(row["payload"]) for row in rows}

    connection = await asyncpg.connect(database_url)
    try:
        identity = await connection.fetchrow(
            "SELECT current_database() AS database, current_user AS role, "
            "current_setting('server_version_num') AS version"
        )
        if (
            identity["database"] != expected_database
            or identity["role"] != "tdnet_ingest"
            or not 180000 <= int(identity["version"]) < 190000
        ):
            raise ValueError("connected database identity is not approved")
        marker = await connection.fetchrow(
            "SELECT instance_name, schema_version FROM pipeline.instance_identity "
            "WHERE instance_name = 'hft_research'"
        )
        if (
            marker is None
            or marker["instance_name"] != "hft_research"
            or marker["schema_version"] != 1
        ):
            raise ValueError("connected database marker is not approved")

        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "tdnet")
            source_ids = list(row_hashes)
            existing = await connection.fetch(
                "SELECT source_id, content_hash, last_observed_at "
                "FROM raw_tdnet.document WHERE source_id = ANY($1::text[]) FOR UPDATE",
                source_ids,
            )
            for current in existing:
                source_id = current["source_id"]
                current_observed = current["last_observed_at"].astimezone(timezone.utc)
                if current_observed == observed_at and current["content_hash"] != row_hashes[source_id]:
                    raise ValueError("equal-time conflicting disclosure")

            await connection.execute(
                """
                INSERT INTO raw_tdnet.list_snapshot
                    (snapshot_hash, target_date, raw_payload,
                     first_observed_at, last_observed_at)
                VALUES ($1, $2, $3::jsonb, $4, $4)
                ON CONFLICT (snapshot_hash) DO UPDATE SET
                    first_observed_at = LEAST(raw_tdnet.list_snapshot.first_observed_at,
                                              EXCLUDED.first_observed_at),
                    last_observed_at = GREATEST(raw_tdnet.list_snapshot.last_observed_at,
                                                EXCLUDED.last_observed_at)
                """,
                capture_hash, target_date, raw_payload_json, observed_at,
            )
            for row in rows:
                payload_json = _canonical(row["payload"])
                await connection.execute(
                    """
                    INSERT INTO raw_tdnet.document
                        (source_id, source_date, title, company_code, published_at,
                         payload, content_hash, snapshot_hash, first_observed_at,
                         last_observed_at, ready_for_analysis)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $9, FALSE)
                    ON CONFLICT (source_id) DO UPDATE SET
                        source_date = CASE WHEN EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at
                                           THEN EXCLUDED.source_date ELSE raw_tdnet.document.source_date END,
                        title = CASE WHEN EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at
                                     THEN EXCLUDED.title ELSE raw_tdnet.document.title END,
                        company_code = CASE WHEN EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at
                                            THEN EXCLUDED.company_code ELSE raw_tdnet.document.company_code END,
                        published_at = CASE WHEN EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at
                                            THEN EXCLUDED.published_at ELSE raw_tdnet.document.published_at END,
                        payload = CASE WHEN EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at
                                       THEN EXCLUDED.payload ELSE raw_tdnet.document.payload END,
                        content_hash = CASE WHEN EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at
                                            THEN EXCLUDED.content_hash ELSE raw_tdnet.document.content_hash END,
                        snapshot_hash = CASE WHEN EXCLUDED.last_observed_at >= raw_tdnet.document.last_observed_at
                                             THEN EXCLUDED.snapshot_hash ELSE raw_tdnet.document.snapshot_hash END,
                        first_observed_at = LEAST(raw_tdnet.document.first_observed_at,
                                                  EXCLUDED.first_observed_at),
                        last_observed_at = GREATEST(raw_tdnet.document.last_observed_at,
                                                    EXCLUDED.last_observed_at)
                    """,
                    row["source_id"], row["source_date"], row["title"], row["company_code"],
                    row["published_at"], payload_json, row_hashes[str(row["source_id"])],
                    capture_hash, observed_at,
                )
    finally:
        await connection.close()

    return {
        "source": "tdnet", "date": capture["target_date"],
        "record_count": len(rows), "snapshot_hash": capture_hash,
        "persisted": True, "ready_for_analysis": False,
    }
