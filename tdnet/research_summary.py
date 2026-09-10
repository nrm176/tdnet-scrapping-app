"""Persist deterministic TDnet Summary extraction from an existing artifact."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg

from tdnet.research_artifacts import (
    _approve_connection,
    _safe_tdnet_url,
    _validate_source_id,
    validate_binary,
)
from tdnet.research_store import validate_database_url
from tdnet.research_summary_parser import (
    PARSER_VERSION,
    PARSER_VERSION_V2,
    parse_summary,
    parse_summary_v2,
)

_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("stored JSON is invalid") from exc
    return value


def _validate_existing(row: object, children: list[object], *, payload: dict[str, object],
                       extraction_hash: str, snapshot_hash: str, source_url: str) -> datetime:
    if (row["artifact_kind"] != "xbrl_zip" or row["extraction_hash"] != extraction_hash
            or _json(row["payload"]) != payload or row["member_path"] != payload["member_path"]
            or row["snapshot_hash"] != snapshot_hash or row["source_url"] != source_url
            or row["ready_for_analysis"] is not False or len(children) != len(payload["facts"])):
        raise ValueError("stored summary extraction is inconsistent")
    for child, fact in zip(children, payload["facts"], strict=True):
        expected = None if fact["value_decimal"] is None else Decimal(str(fact["value_decimal"]))
        if (child["ordinal"] != fact["ordinal"] or child["concept_qname"] != fact["concept_qname"]
                or child["context_id"] != fact["context_id"] or child["unit_id"] != fact["unit_id"]
                or child["numeric_value"] != expected or child["is_nil"] is not fact["is_nil"]
                or _json(child["payload"]) != fact or child["ready_for_analysis"] is not False):
            raise ValueError("stored summary facts are inconsistent")
    return row["extracted_at"]


def _parse_for_version(content: bytes, parser_version: str) -> dict[str, object]:
    if parser_version == PARSER_VERSION:
        return parse_summary(content)
    if parser_version == PARSER_VERSION_V2:
        return parse_summary_v2(content)
    raise ValueError("unsupported TDnet Summary parser version")


async def ingest_summary(database_url: str, source_id: str, content_hash: str,
                         *, parser_version: str = PARSER_VERSION) -> dict[str, object]:
    """Parse and atomically store one exact, already-persisted XBRL ZIP."""
    _validate_source_id(source_id)
    if parser_version not in (PARSER_VERSION, PARSER_VERSION_V2):
        raise ValueError("unsupported TDnet Summary parser version")
    if not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash):
        raise ValueError("invalid content hash")
    expected_database = validate_database_url(database_url)
    connection = await asyncpg.connect(database_url)
    try:
        await _approve_connection(connection, expected_database)
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                                     f"tdnet-summary:{source_id}:{content_hash}:{parser_version}")
            artifact = await connection.fetchrow(
                """SELECT artifact.content, artifact.byte_length, artifact.source_url,
                          artifact.snapshot_hash, document.snapshot_hash AS document_snapshot_hash,
                          document.payload AS document_payload
                   FROM raw_tdnet.document_artifact AS artifact
                   JOIN raw_tdnet.document AS document ON document.source_id = artifact.source_id
                   WHERE artifact.source_id = $1 AND artifact.artifact_kind = 'xbrl_zip'
                     AND artifact.content_hash = $2
                   FOR UPDATE OF artifact, document""", source_id, content_hash)
            if artifact is None:
                raise ValueError("stored artifact is unavailable")
            content = bytes(artifact["content"])
            expected_url = _safe_tdnet_url(artifact["source_url"], "xbrl_zip")
            if (artifact["byte_length"] != len(content)
                    or validate_binary(content, "xbrl_zip") != content_hash):
                raise ValueError("stored artifact provenance is invalid")
            payload = _parse_for_version(content, parser_version)
            payload_json = _canonical(payload)
            extraction_hash = hashlib.sha256(payload_json.encode()).hexdigest()
            existing = await connection.fetchrow(
                """SELECT artifact_kind, snapshot_hash, source_url, member_path, extraction_hash,
                          payload, extracted_at, ready_for_analysis
                   FROM raw_tdnet.summary_extraction
                   WHERE source_id = $1 AND content_hash = $2 AND parser_version = $3 FOR UPDATE""",
                source_id, content_hash, parser_version)
            if existing is not None:
                children = await connection.fetch(
                    """SELECT ordinal, concept_qname, context_id, unit_id, numeric_value,
                              is_nil, payload, ready_for_analysis
                       FROM raw_tdnet.summary_fact
                       WHERE source_id = $1 AND content_hash = $2 AND parser_version = $3
                       ORDER BY ordinal FOR UPDATE""", source_id, content_hash, parser_version)
                extracted_at = _validate_existing(existing, list(children), payload=payload,
                                                  extraction_hash=extraction_hash,
                                                  snapshot_hash=artifact["snapshot_hash"], source_url=expected_url)
            else:
                extracted_at = _utc_now()
                await connection.execute(
                    """INSERT INTO raw_tdnet.summary_extraction
                    (source_id, content_hash, parser_version, artifact_kind, snapshot_hash,
                            source_url, member_path, extraction_hash, payload, extracted_at,
                            ready_for_analysis)
                       VALUES ($1, $2, $3, 'xbrl_zip', $4, $5, $6, $7, $8::jsonb, $9, FALSE)""",
                    source_id, content_hash, parser_version, artifact["snapshot_hash"], expected_url,
                    payload["member_path"], extraction_hash, payload_json, extracted_at)
                for fact in payload["facts"]:
                    await connection.execute(
                        """INSERT INTO raw_tdnet.summary_fact
                               (source_id, content_hash, parser_version, ordinal, concept_qname,
                                context_id, numeric_value, unit_id, is_nil, payload, ready_for_analysis)
                           VALUES ($1, $2, $3, $4, $5, $6, $7::numeric, $8, $9, $10::jsonb, FALSE)""",
                        source_id, content_hash, parser_version, fact["ordinal"], fact["concept_qname"],
                        fact["context_id"], fact["value_decimal"], fact["unit_id"], fact["is_nil"],
                        _canonical(fact))
    finally:
        await connection.close()
    return {"source": "tdnet", "source_id": source_id, "content_hash": content_hash,
            "parser_version": parser_version, "extraction_hash": extraction_hash,
            "selected_fact_count": payload["selected_fact_count"], "payload": payload,
            "extracted_at": extracted_at.isoformat(), "persisted": True, "ready_for_analysis": False}


def _parser() -> argparse.ArgumentParser:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise ValueError("invalid command-line arguments")
    parser = SafeParser(description="Extract one stored TDnet Summary XBRL artifact")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--content-hash", required=True)
    parser.add_argument("--parser-version", choices=(PARSER_VERSION, PARSER_VERSION_V2),
                        default=PARSER_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        database_url = os.environ.get("HFT_RESEARCH_DATABASE_URL")
        if not database_url:
            raise ValueError("research database is not configured")
        print(json.dumps(asyncio.run(ingest_summary(
            database_url, args.source_id, args.content_hash, parser_version=args.parser_version)),
                         ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
