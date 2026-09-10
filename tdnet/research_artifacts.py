"""Validate and persist bounded TDnet document artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import lzma
import os
import re
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import asyncpg
import requests

from tdnet.research_store import validate_database_url


_MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
_MAX_ZIP_MEMBER_BYTES = 20 * 1024 * 1024
_MAX_ZIP_EXPANDED_BYTES = 100 * 1024 * 1024
_MAX_ZIP_MEMBERS = 1024
_DOWNLOAD_ROOT = Path("/Volumes/yakushimachi/Downloads")
_INLINE_XBRL_NAMESPACES = (
    b"http://www.xbrl.org/2008/inlineXBRL",
    b"http://www.xbrl.org/2013/inlineXBRL",
)
_SOURCE_ID = re.compile(r"^/inbs/[0-9]+\.pdf$")
_ARTIFACT_PATH = {
    "pdf": re.compile(r"^/inbs/[0-9]+\.pdf$"),
    "xbrl_zip": re.compile(r"^/inbs/[0-9]+\.zip$"),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _invalid_binary() -> ValueError:
    return ValueError("invalid artifact content")


def validate_binary(content: bytes, kind: str) -> str:
    """Return the exact SHA-256 for a minimally valid, bounded artifact."""
    if not isinstance(content, bytes) or not content or len(content) > _MAX_ARTIFACT_BYTES:
        raise _invalid_binary()
    if kind == "pdf":
        if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
            raise _invalid_binary()
    elif kind == "xbrl_zip":
        _validate_xbrl_zip(content)
    else:
        raise ValueError("invalid artifact kind")
    return hashlib.sha256(content).hexdigest()


def _validate_xbrl_zip(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if not members or len(members) > _MAX_ZIP_MEMBERS:
                raise _invalid_binary()
            expanded = 0
            has_xbrl = False
            for member in members:
                normalized = member.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if (
                    not normalized
                    or normalized.startswith("/")
                    or path.is_absolute()
                    or ".." in path.parts
                    or re.match(r"^[A-Za-z]:", normalized)
                    or member.flag_bits & 0x1
                    or member.file_size > _MAX_ZIP_MEMBER_BYTES
                ):
                    raise _invalid_binary()
                expanded += member.file_size
                if expanded > _MAX_ZIP_EXPANDED_BYTES:
                    raise _invalid_binary()
                lowered = normalized.lower()
                has_xbrl = has_xbrl or (not member.is_dir() and lowered.endswith(".xbrl"))
                inspect_inline = not member.is_dir() and lowered.endswith((".htm", ".html"))
                inline_content = bytearray()
                actual = 0
                with archive.open(member) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        actual += len(chunk)
                        if actual > _MAX_ZIP_MEMBER_BYTES:
                            raise _invalid_binary()
                        if inspect_inline:
                            inline_content.extend(chunk)
                if actual != member.file_size:
                    raise _invalid_binary()
                if inspect_inline and any(
                    namespace in inline_content for namespace in _INLINE_XBRL_NAMESPACES
                ):
                    has_xbrl = True
            if not has_xbrl:
                raise _invalid_binary()
    except ValueError:
        raise
    except (
        EOFError,
        OSError,
        RuntimeError,
        lzma.LZMAError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as exc:
        raise _invalid_binary() from exc


def _safe_tdnet_url(value: object, kind: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid artifact URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid artifact URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.release.tdnet.info"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not _ARTIFACT_PATH[kind].fullmatch(parsed.path)
    ):
        raise ValueError("invalid artifact URL")
    return value


def _validate_source_id(source_id: str) -> None:
    url = _safe_tdnet_url(source_id, "pdf")
    if not _SOURCE_ID.fullmatch(urlsplit(url).path):
        raise ValueError("invalid source ID")


async def _approve_connection(connection: asyncpg.Connection, expected_database: str) -> None:
    identity = await connection.fetchrow(
        "SELECT current_database() AS database, current_user AS role, "
        "current_setting('server_version_num') AS version"
    )
    if (
        identity is None
        or identity["database"] != expected_database
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


def _expected_url(document: object, source_id: str, kind: str) -> tuple[str, str]:
    if document is None or document["source_id"] != source_id:
        raise ValueError("source document is unavailable")
    payload = document["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("source metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("source metadata is invalid")
    if kind == "pdf":
        value = payload.get("pdf_url")
        if value != source_id:
            raise ValueError("source metadata is invalid")
    else:
        if payload.get("xbrl_available") is not True:
            raise ValueError("artifact is unavailable")
        value = payload.get("xbrl_url")
    return _safe_tdnet_url(value, kind), str(document["snapshot_hash"])


def _read_bounded(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("input is not a regular file")
    with path.open("rb") as stream:
        content = stream.read(_MAX_ARTIFACT_BYTES + 1)
    if len(content) > _MAX_ARTIFACT_BYTES:
        raise _invalid_binary()
    return content


def _read_named_input(path: Path, expected_url: str) -> bytes:
    if path.name != Path(urlsplit(expected_url).path).name:
        raise ValueError("input filename does not match source metadata")
    return _read_bounded(path)


def _local_candidates(document: object, expected_url: str) -> list[Path]:
    pdf_stem = Path(urlsplit(str(document["source_id"])).path).stem
    filename = Path(urlsplit(expected_url).path).name
    return [
        _DOWNLOAD_ROOT / "tdnet" / pdf_stem / filename,
        _DOWNLOAD_ROOT / "tdnet-forecast-correction" / pdf_stem / filename,
    ]


def _fetch_bytes(expected_url: str) -> bytes:
    response = None
    try:
        response = requests.get(
            expected_url, timeout=30, allow_redirects=False, stream=True,
        )
        if response.status_code != 200:
            raise ValueError("TDnet returned a non-success status")
        length = response.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > _MAX_ARTIFACT_BYTES:
                    raise _invalid_binary()
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid TDnet response metadata") from exc
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not isinstance(chunk, bytes):
                raise ValueError("invalid TDnet response")
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_ARTIFACT_BYTES:
                raise _invalid_binary()
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException as exc:
        raise ValueError("TDnet request failed") from exc
    finally:
        if response is not None:
            response.close()


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise ValueError("output destination is unavailable")
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise ValueError("output already exists") from exc
    except OSError as exc:
        raise ValueError("output destination is unavailable") from exc


def _preflight_output(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError("output destination is unavailable") from exc
    else:
        raise ValueError("output already exists")
    if not path.parent.is_dir() or not os.access(path.parent, os.W_OK):
        raise ValueError("output destination is unavailable")


async def _acquire(
    connection: asyncpg.Connection,
    document: object,
    source_id: str,
    kind: str,
    expected_url: str,
    input_path: Path | None,
) -> tuple[bytes, str]:
    if input_path is not None:
        return await asyncio.to_thread(_read_named_input, input_path, expected_url), "local"

    for candidate in _local_candidates(document, expected_url):
        if candidate.is_symlink():
            raise ValueError("input is not a regular file")
        if candidate.is_file() and candidate.stat().st_size > 0:
            return await asyncio.to_thread(_read_named_input, candidate, expected_url), "local"

    saved = await connection.fetch(
        "SELECT content, content_hash, source_url, snapshot_hash "
        "FROM raw_tdnet.document_artifact "
        "WHERE source_id = $1 AND artifact_kind = $2 "
        "ORDER BY first_observed_at, content_hash",
        source_id, kind,
    )
    if len(saved) > 1:
        raise ValueError("multiple artifact versions require an explicit input")
    if len(saved) == 1:
        content = bytes(saved[0]["content"])
        if (
            validate_binary(content, kind) != saved[0]["content_hash"]
            or saved[0]["source_url"] != expected_url
        ):
            raise ValueError("stored artifact is inconsistent")
        return content, "database"

    return await asyncio.to_thread(_fetch_bytes, expected_url), "http"


async def ingest_artifact(
    database_url: str,
    source_id: str,
    kind: str,
    *,
    input_path: Path | str | None = None,
    fetch: bool = False,
    output_path: Path | str | None = None,
) -> dict[str, object]:
    """Acquire one artifact and atomically append or re-observe its version."""
    if kind not in _ARTIFACT_PATH or (input_path is None) == (not fetch):
        raise ValueError("choose exactly one acquisition method")
    _validate_source_id(source_id)
    expected_database = validate_database_url(database_url)
    input_file = Path(input_path) if input_path is not None else None
    output_file = Path(output_path) if output_path is not None else None
    if output_file is not None:
        _preflight_output(output_file)

    connection = await asyncpg.connect(database_url)
    try:
        await _approve_connection(connection, expected_database)
        document = await connection.fetchrow(
            "SELECT source_id, snapshot_hash, title, payload "
            "FROM raw_tdnet.document WHERE source_id = $1",
            source_id,
        )
        expected_url, initial_snapshot = _expected_url(document, source_id, kind)
        content, method = await _acquire(
            connection, document, source_id, kind, expected_url, input_file,
        )
        content_hash = validate_binary(content, kind)
        observed_at = _utc_now()
        if output_file is not None:
            await asyncio.to_thread(_write_exclusive, output_file, content)

        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"tdnet-artifact:{source_id}:{kind}",
            )
            current = await connection.fetchrow(
                "SELECT source_id, snapshot_hash, title, payload "
                "FROM raw_tdnet.document WHERE source_id = $1 FOR UPDATE",
                source_id,
            )
            current_url, current_snapshot = _expected_url(current, source_id, kind)
            if current_snapshot != initial_snapshot or current_url != expected_url:
                raise ValueError("source metadata changed during acquisition")
            existing = await connection.fetchrow(
                "SELECT content, source_url FROM raw_tdnet.document_artifact "
                "WHERE source_id = $1 AND artifact_kind = $2 AND content_hash = $3 FOR UPDATE",
                source_id, kind, content_hash,
            )
            if existing is not None and (
                bytes(existing["content"]) != content or existing["source_url"] != expected_url
            ):
                raise ValueError("artifact hash conflicts with stored content")
            await connection.execute(
                """
                INSERT INTO raw_tdnet.document_artifact
                    (source_id, artifact_kind, content_hash, content, byte_length,
                     source_url, snapshot_hash, first_observed_at, last_observed_at,
                     ready_for_analysis)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, FALSE)
                ON CONFLICT (source_id, artifact_kind, content_hash) DO UPDATE SET
                    first_observed_at = LEAST(raw_tdnet.document_artifact.first_observed_at,
                                              EXCLUDED.first_observed_at),
                    last_observed_at = GREATEST(raw_tdnet.document_artifact.last_observed_at,
                                                EXCLUDED.last_observed_at)
                """,
                source_id, kind, content_hash, content, len(content), expected_url,
                initial_snapshot, observed_at,
            )
    finally:
        await connection.close()

    return {
        "source": "tdnet",
        "source_id": source_id,
        "kind": kind,
        "content_hash": content_hash,
        "byte_length": len(content),
        "snapshot_hash": initial_snapshot,
        "persisted": True,
        "ready_for_analysis": False,
        "acquisition_method": method,
    }


def _parser() -> argparse.ArgumentParser:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise ValueError("invalid command-line arguments")

    parser = SafeParser(description="Store one validated TDnet document artifact")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--kind", required=True, choices=sorted(_ARTIFACT_PATH))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--fetch", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        database_url = os.environ.get("HFT_RESEARCH_DATABASE_URL")
        if not database_url:
            raise ValueError("research database is not configured")
        result = asyncio.run(
            ingest_artifact(
                database_url,
                args.source_id,
                args.kind,
                input_path=args.input,
                fetch=args.fetch,
                output_path=args.output,
            )
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
