from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.models import TdnetDisclosure
from tdnet.orm import Base, DisclosureRecord, DocumentParseJobRecord
from tdnet.parsers import ParsedPdfArtifacts, normalize_markdown, parse_pending_files
from tdnet.repository import (
    complete_disclosure_file,
    get_or_create_disclosure_file,
    iter_files_for_parse,
    upsert_disclosures,
)


async def _create_completed_pdf(session, storage_path: Path) -> int:
    disclosure = TdnetDisclosure(
        time="15:30",
        code="12345",
        name="テスト株式会社",
        title="業績予想の修正に関するお知らせ",
        pdf_url="https://www.release.tdnet.info/inbs/140120251021001001.pdf",
        xbrl_available=False,
        place="東",
        history="",
        disclosure_date=date(2025, 10, 21),
    )
    await upsert_disclosures(session, [disclosure])
    record = await session.get(DisclosureRecord, disclosure.id)
    assert record is not None
    file_record = await get_or_create_disclosure_file(
        session,
        disclosure=record,
        file_type="pdf",
        source_url=str(disclosure.pdf_url),
        source_file_id="140120251021001001",
        storage_bucket="tdnet-forecast-correction",
        storage_path=str(storage_path),
    )
    await complete_disclosure_file(
        session,
        file_record,
        file_size_bytes=10,
        sha256="a" * 64,
        content_type="application/pdf",
    )
    return file_record.id


def test_normalize_markdown_collapses_repeated_blanks():
    assert normalize_markdown("  a  \n\n\n| b |\r\n\nc") == "  a\n\n| b |\n\nc\n"


@pytest.mark.asyncio
async def test_parse_pending_files_marks_completed_and_excludes_next_run(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def fake_parse(pdf_path: Path, *, parser_name: str, parser_version: str):
        markdown_path = pdf_path.parent / "parsed" / f"{parser_name}.{parser_version}.md"
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text("parsed text\n", encoding="utf-8")
        return ParsedPdfArtifacts(
            markdown_path=markdown_path,
            pages_path=markdown_path.with_suffix(".pages.json"),
            metadata_path=markdown_path.with_suffix(".meta.json"),
            markdown_sha256="b" * 64,
            page_count=1,
            char_count=12,
        )

    monkeypatch.setattr("tdnet.parsers.parse_pdf_to_artifacts", fake_parse)

    async with session_factory() as session:
        file_id = await _create_completed_pdf(session, pdf_path)
        candidates = await iter_files_for_parse(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
        )
        assert [candidate.id for candidate in candidates] == [file_id]

        summary = await parse_pending_files(
            session,
            parser_version="test-version",
        )
        assert summary.candidates == 1
        assert summary.parsed == 1
        assert summary.failed == 0

        next_candidates = await iter_files_for_parse(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
        )
        assert next_candidates == []

        job = await session.scalar(select(DocumentParseJobRecord))
        assert job is not None
        assert job.parse_status == "completed"
        assert job.text_path is not None
        assert job.text_sha256 == "b" * 64

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_parse_jobs_are_only_selected_for_retry(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def failing_parse(pdf_path: Path, *, parser_name: str, parser_version: str):
        raise RuntimeError("broken pdf")

    monkeypatch.setattr("tdnet.parsers.parse_pdf_to_artifacts", failing_parse)

    async with session_factory() as session:
        file_id = await _create_completed_pdf(session, pdf_path)
        summary = await parse_pending_files(
            session,
            parser_version="test-version",
        )
        assert summary.failed == 1

        candidates = await iter_files_for_parse(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
        )
        assert candidates == []

        retry_candidates = await iter_files_for_parse(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            retry_failed=True,
        )
        assert [candidate.id for candidate in retry_candidates] == [file_id]

    await engine.dispose()
