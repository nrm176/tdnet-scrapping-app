import hashlib
import io
import json
import os
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import tdnet.research_artifacts as artifacts
from tdnet.research_ingest import store_capture
from tests.test_research_ingest import capture, html


DATABASE_URL = "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research"
PDF_URL = "https://www.release.tdnet.info/inbs/140120260904000001.pdf"
XBRL_URL = "https://www.release.tdnet.info/inbs/081220260904000001.zip"
PDF = b"%PDF-1.4\n%%EOF\n"


def xbrl_zip(name="PublicDoc/report.xbrl", content=b"<xbrl/>", compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression) as archive:
        archive.writestr(name, content)
    return stream.getvalue()


def test_binary_hash_is_exact():
    assert artifacts.validate_binary(PDF, "pdf") == hashlib.sha256(PDF).hexdigest()


def test_validate_binary_accepts_real_zip_with_xbrl_member():
    content = xbrl_zip()
    assert artifacts.validate_binary(content, "xbrl_zip") == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("namespace", [
    b"http://www.xbrl.org/2008/inlineXBRL",
    b"http://www.xbrl.org/2013/inlineXBRL",
])
def test_validate_binary_accepts_tdnet_inline_xbrl_html(namespace):
    content = xbrl_zip("PublicDoc/report-ixbrl.htm", b"<html xmlns:ix=\"" + namespace + b"\"></html>")
    assert artifacts.validate_binary(content, "xbrl_zip") == hashlib.sha256(content).hexdigest()


def test_validate_binary_rejects_plain_html_zip():
    with pytest.raises(ValueError):
        artifacts.validate_binary(xbrl_zip("PublicDoc/report.htm", b"<html>ordinary page</html>"), "xbrl_zip")


@pytest.mark.parametrize("content", [b"", b"<html>error</html>", b'{"message":"error"}', b"%PDF-1.4\n"])
def test_validate_pdf_rejects_empty_non_pdf_and_missing_eof(content):
    with pytest.raises(ValueError):
        artifacts.validate_binary(content, "pdf")


@pytest.mark.parametrize("content", [b"PK not a zip", xbrl_zip("readme.txt")])
def test_validate_zip_rejects_corrupt_and_non_xbrl_archives(content):
    with pytest.raises(ValueError):
        artifacts.validate_binary(content, "xbrl_zip")


def test_validate_zip_normalizes_corrupt_deflate_stream_to_value_error():
    content = bytearray(xbrl_zip(content=b"A" * 1000))
    name_length = int.from_bytes(content[26:28], "little")
    extra_length = int.from_bytes(content[28:30], "little")
    compressed_offset = 30 + name_length + extra_length
    content[compressed_offset] = (content[compressed_offset] & 0xF8) | 0x07
    with pytest.raises(ValueError):
        artifacts.validate_binary(bytes(content), "xbrl_zip")


def test_validate_zip_normalizes_corrupt_lzma_stream_to_value_error():
    content = bytearray(xbrl_zip(content=b"A" * 1000, compression=zipfile.ZIP_LZMA))
    name_length = int.from_bytes(content[26:28], "little")
    extra_length = int.from_bytes(content[28:30], "little")
    compressed_offset = 30 + name_length + extra_length
    content[compressed_offset + 12] ^= 0xFF
    with pytest.raises(ValueError):
        artifacts.validate_binary(bytes(content), "xbrl_zip")


@pytest.mark.parametrize("name", ["../report.xbrl", "/report.xbrl", "safe/../../report.xbrl", r"..\report.xbrl"])
def test_validate_zip_rejects_traversal_and_absolute_members(name):
    with pytest.raises(ValueError):
        artifacts.validate_binary(xbrl_zip(name), "xbrl_zip")


def test_validate_zip_rejects_encrypted_flag_and_oversized_member_metadata():
    encrypted = bytearray(xbrl_zip())
    local = encrypted.find(b"PK\x03\x04")
    central = encrypted.find(b"PK\x01\x02")
    encrypted[local + 6:local + 8] = (1).to_bytes(2, "little")
    encrypted[central + 8:central + 10] = (1).to_bytes(2, "little")
    with pytest.raises(ValueError):
        artifacts.validate_binary(bytes(encrypted), "xbrl_zip")

    oversized = bytearray(xbrl_zip())
    central = oversized.find(b"PK\x01\x02")
    oversized[central + 24:central + 28] = (20 * 1024 * 1024 + 1).to_bytes(4, "little")
    with pytest.raises(ValueError):
        artifacts.validate_binary(bytes(oversized), "xbrl_zip")


def test_validate_binary_rejects_artifact_over_20_mib():
    content = b"%PDF-1.4\n" + b"x" * (20 * 1024 * 1024) + b"%%EOF\n"
    with pytest.raises(ValueError):
        artifacts.validate_binary(content, "pdf")


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *unused):
        return False


class Connection:
    def __init__(self, *, document=None, saved=(), changed_snapshot=None):
        self.document = document
        self.saved = list(saved)
        self.changed_snapshot = changed_snapshot
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
        if "FROM raw_tdnet.document " in sql:
            if "FOR UPDATE" in sql and self.document is not None and self.changed_snapshot:
                return {**self.document, "snapshot_hash": self.changed_snapshot}
            return self.document
        if "FROM raw_tdnet.document_artifact" in sql and "content_hash" in sql:
            wanted = args[2]
            return next((row for row in self.saved if row["content_hash"] == wanted), None)
        return None

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM raw_tdnet.document_artifact" in sql:
            return self.saved
        return []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "INSERT 0 1"

    async def close(self):
        self.closed = True


def document(*, source_id=PDF_URL, snapshot_hash="a" * 64, xbrl=False):
    return {
        "source_id": source_id,
        "snapshot_hash": snapshot_hash,
        "title": "決算",
        "payload": {
            "pdf_url": source_id,
            "xbrl_available": xbrl,
            "xbrl_url": XBRL_URL if xbrl else None,
        },
    }


@pytest.mark.asyncio
async def test_local_ingest_matches_db_url_and_uses_guarded_atomic_upsert(tmp_path):
    source = tmp_path / Path(PDF_URL).name
    source.write_bytes(PDF)
    row = document()
    row["payload"] = json.dumps(row["payload"])
    connection = Connection(document=row)
    with patch.object(artifacts.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch.object(artifacts, "_utc_now", return_value=datetime(2026, 9, 6, tzinfo=timezone.utc)):
        result = await artifacts.ingest_artifact(DATABASE_URL, PDF_URL, "pdf", input_path=source)

    assert result == {
        "source": "tdnet", "source_id": PDF_URL, "kind": "pdf",
        "content_hash": hashlib.sha256(PDF).hexdigest(), "byte_length": len(PDF),
        "snapshot_hash": "a" * 64,
        "persisted": True, "ready_for_analysis": False, "acquisition_method": "local",
    }
    sql = "\n".join(call[1] for call in connection.calls)
    assert "pg_advisory_xact_lock" in sql
    assert "ON CONFLICT (source_id, artifact_kind, content_hash) DO UPDATE" in sql
    assert "LEAST(raw_tdnet.document_artifact.first_observed_at" in sql
    assert "GREATEST(raw_tdnet.document_artifact.last_observed_at" in sql
    assert connection.closed


@pytest.mark.asyncio
async def test_ingest_rejects_wrong_filename_symlink_and_snapshot_change(tmp_path):
    wrong = tmp_path / "other.pdf"
    wrong.write_bytes(PDF)
    link = tmp_path / Path(PDF_URL).name
    link.symlink_to(wrong)
    for input_path, changed in ((wrong, None), (link, None), (tmp_path / Path(PDF_URL).name, "b" * 64)):
        if changed:
            link.unlink()
            input_path.write_bytes(PDF)
        connection = Connection(document=document(), changed_snapshot=changed)
        with patch.object(artifacts.asyncpg, "connect", new=AsyncMock(return_value=connection)), pytest.raises(ValueError):
            await artifacts.ingest_artifact(DATABASE_URL, PDF_URL, "pdf", input_path=input_path)
        assert not any("INSERT INTO raw_tdnet.document_artifact" in call[1] for call in connection.calls)


@pytest.mark.asyncio
async def test_fetch_rejects_invalid_id_url_and_missing_document_without_http():
    cases = [
        ("not-a-source-id", None),
        (PDF_URL, document(source_id=PDF_URL.replace("https://", "http://"))),
        (PDF_URL, None),
        (PDF_URL, document(source_id=PDF_URL.replace(".pdf", "x.pdf"))),
    ]
    for source_id, row in cases:
        connection = Connection(document=row)
        with patch.object(artifacts.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
             patch.object(artifacts.requests, "get") as get, pytest.raises(ValueError):
            await artifacts.ingest_artifact(DATABASE_URL, source_id, "pdf", fetch=True)
        get.assert_not_called()


class Response:
    def __init__(self, content, *, status=200, headers=None):
        self.content = content
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size <= 1024 * 1024
        yield self.content

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_fetch_streams_fixed_db_url_rejects_json_and_closes_response(tmp_path):
    output = tmp_path / "artifact.pdf"
    response = Response(b'{"error":"PRIVATE_SENTINEL"}')
    connection = Connection(document=document())
    with patch.object(artifacts.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch.object(artifacts.requests, "get", return_value=response) as get, pytest.raises(ValueError) as error:
        await artifacts.ingest_artifact(DATABASE_URL, PDF_URL, "pdf", fetch=True, output_path=output)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    get.assert_called_once_with(PDF_URL, timeout=30, allow_redirects=False, stream=True)
    assert response.closed and not output.exists()


@pytest.mark.asyncio
async def test_fetch_reuses_one_database_artifact_without_http(tmp_path):
    digest = hashlib.sha256(PDF).hexdigest()
    saved = [{"content": PDF, "content_hash": digest, "source_url": PDF_URL,
              "snapshot_hash": "a" * 64}]
    connection = Connection(document=document(), saved=saved)
    output = tmp_path / "saved.pdf"
    with patch.object(artifacts.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch.object(artifacts.requests, "get") as get:
        result = await artifacts.ingest_artifact(
            DATABASE_URL, PDF_URL, "pdf", fetch=True, output_path=output,
        )
    get.assert_not_called()
    assert result["acquisition_method"] == "database"
    assert output.read_bytes() == PDF


@pytest.mark.asyncio
async def test_fetch_prefers_established_local_download_over_database_copy(tmp_path):
    local = tmp_path / "tdnet" / Path(PDF_URL).stem / Path(PDF_URL).name
    local.parent.mkdir(parents=True)
    local.write_bytes(PDF)
    stored = b"%PDF-1.7\nstored\n%%EOF\n"
    saved = [{"content": stored, "content_hash": hashlib.sha256(stored).hexdigest(),
              "source_url": PDF_URL, "snapshot_hash": "a" * 64}]
    connection = Connection(document=document(), saved=saved)
    with patch.object(artifacts, "_DOWNLOAD_ROOT", tmp_path), \
         patch.object(artifacts.asyncpg, "connect", new=AsyncMock(return_value=connection)), \
         patch.object(artifacts.requests, "get") as get:
        result = await artifacts.ingest_artifact(DATABASE_URL, PDF_URL, "pdf", fetch=True)
    get.assert_not_called()
    assert result["acquisition_method"] == "local"
    assert result["content_hash"] == hashlib.sha256(PDF).hexdigest()


@pytest.mark.asyncio
async def test_dangling_output_symlink_is_rejected_before_database_or_http(tmp_path):
    output = tmp_path / "dangling.pdf"
    output.symlink_to(tmp_path / "missing-target.pdf")
    with patch.object(artifacts.asyncpg, "connect", new=AsyncMock()) as connect, \
         patch.object(artifacts.requests, "get") as get, pytest.raises(ValueError):
        await artifacts.ingest_artifact(
            DATABASE_URL, PDF_URL, "pdf", fetch=True, output_path=output,
        )
    connect.assert_not_awaited()
    get.assert_not_called()


@pytest.mark.asyncio
async def test_missing_output_parent_is_rejected_before_database_or_http(tmp_path):
    output = tmp_path / "missing-parent" / "artifact.pdf"
    with patch.object(artifacts.asyncpg, "connect", new=AsyncMock()) as connect, \
         patch.object(artifacts.requests, "get") as get, pytest.raises(ValueError):
        await artifacts.ingest_artifact(
            DATABASE_URL, PDF_URL, "pdf", fetch=True, output_path=output,
        )
    connect.assert_not_awaited()
    get.assert_not_called()


def test_cli_outputs_sanitized_json_and_never_echoes_values(tmp_path, capsys, monkeypatch):
    sentinel = "PRIVATE_SENTINEL"
    monkeypatch.setenv("HFT_RESEARCH_DATABASE_URL", sentinel)
    assert artifacts.main(["--source-id", sentinel, "--kind", "pdf", "--fetch"]) != 0
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": "ValueError"}
    assert sentinel not in captured.err


TEST_DSN = os.environ.get("HFT_DISCLOSURE_TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DSN, reason="requires controller disposable PostgreSQL DSN")
@pytest.mark.asyncio
async def test_pg18_local_pdf_history_idempotency_and_atomic_rejections(tmp_path):
    digits = str(uuid.uuid4().int)[:20]
    filename = f"{digits}.pdf"
    source_id = f"https://www.release.tdnet.info/inbs/{filename}"
    observed = datetime.now(timezone.utc).isoformat()
    await store_capture(capture(html(href=filename), observed=observed), TEST_DSN)
    source = tmp_path / filename
    source.write_bytes(PDF)

    first = await artifacts.ingest_artifact(TEST_DSN, source_id, "pdf", input_path=source)
    again = await artifacts.ingest_artifact(TEST_DSN, source_id, "pdf", input_path=source)
    assert first["content_hash"] == again["content_hash"]

    changed = b"%PDF-1.7\nchanged\n%%EOF\n"
    source.write_bytes(changed)
    await artifacts.ingest_artifact(TEST_DSN, source_id, "pdf", input_path=source)
    connection = await artifacts.asyncpg.connect(TEST_DSN)
    try:
        rows = await connection.fetch(
            "SELECT content, content_hash, snapshot_hash FROM raw_tdnet.document_artifact "
            "WHERE source_id = $1 ORDER BY content_hash", source_id,
        )
        assert len(rows) == 2
        assert {bytes(row["content"]) for row in rows} == {PDF, changed}
        assert {row["content_hash"] for row in rows} == {
            hashlib.sha256(PDF).hexdigest(), hashlib.sha256(changed).hexdigest(),
        }
        assert len({row["snapshot_hash"] for row in rows}) == 1
    finally:
        await connection.close()

    source.write_bytes(b"<html>bad</html>")
    with pytest.raises(ValueError):
        await artifacts.ingest_artifact(TEST_DSN, source_id, "pdf", input_path=source)
    missing_id = f"https://www.release.tdnet.info/inbs/{str(uuid.uuid4().int)[:20]}.pdf"
    missing_path = tmp_path / Path(missing_id).name
    missing_path.write_bytes(PDF)
    with pytest.raises(ValueError):
        await artifacts.ingest_artifact(TEST_DSN, missing_id, "pdf", input_path=missing_path)
    connection = await artifacts.asyncpg.connect(TEST_DSN)
    try:
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.document_artifact WHERE source_id = $1", source_id,
        ) == 2
        assert await connection.fetchval(
            "SELECT count(*) FROM raw_tdnet.document_artifact WHERE source_id = $1", missing_id,
        ) == 0
    finally:
        await connection.close()
