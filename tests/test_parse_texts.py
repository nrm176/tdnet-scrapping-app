from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.models import TdnetDisclosure
from tdnet.orm import Base, DisclosureRecord, DocumentParseJobRecord, DocumentParseTextRecord
from tdnet.parse_texts import (
    backfill_parse_texts,
    load_parse_text_payload,
    persist_parse_text_artifacts,
)
from tdnet.repository import (
    complete_disclosure_file,
    get_or_create_disclosure_file,
    upsert_disclosures,
)


async def _create_parse_job(
    session,
    tmp_path: Path,
    *,
    source_file_id: str = "140120260515538453",
    parser_name: str = "pymupdf4llm",
    parser_version: str = "test-version",
    content: str = "parsed text\n",
) -> tuple[DocumentParseJobRecord, Path]:
    disclosure = TdnetDisclosure(
        time="15:30",
        code="12345",
        name="テスト株式会社",
        title="業績予想の修正に関するお知らせ",
        pdf_url=f"https://www.release.tdnet.info/inbs/{source_file_id}.pdf",
        xbrl_available=False,
        place="東",
        history="",
        disclosure_date=date(2026, 5, 16),
    )
    await upsert_disclosures(session, [disclosure])
    record = await session.get(DisclosureRecord, disclosure.id)
    assert record is not None

    pdf_path = tmp_path / source_file_id / f"{source_file_id}.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")
    file_record = await get_or_create_disclosure_file(
        session,
        disclosure=record,
        file_type="pdf",
        source_url=str(disclosure.pdf_url),
        source_file_id=source_file_id,
        storage_bucket="tdnet",
        storage_path=str(pdf_path),
    )
    await complete_disclosure_file(
        session,
        file_record,
        file_size_bytes=10,
        sha256="a" * 64,
        content_type="application/pdf",
    )

    markdown_path = pdf_path.parent / "parsed" / f"{parser_name}.{parser_version}.md"
    markdown_path.parent.mkdir()
    markdown_path.write_text(content, encoding="utf-8")
    markdown_path.with_suffix(".pages.json").write_text(
        json.dumps(
            {"pages": [{"page": 1, "markdown": content, "char_count": len(content)}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    parse_job = DocumentParseJobRecord(
        file_id=file_record.id,
        parser_name=parser_name,
        parser_version=parser_version,
        parse_status="completed",
        text_path=str(markdown_path),
        text_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    session.add(parse_job)
    await session.commit()
    await session.refresh(parse_job)
    return parse_job, markdown_path


def test_load_parse_text_payload_reads_markdown_and_pages(tmp_path):
    markdown_path = tmp_path / "pymupdf4llm.test.md"
    markdown_path.write_text("hello\n", encoding="utf-8")
    markdown_path.with_suffix(".pages.json").write_text(
        '{"pages":[{"page":1,"markdown":"hello\\n","char_count":6}]}',
        encoding="utf-8",
    )

    payload = load_parse_text_payload(markdown_path)

    assert payload.content_text == "hello\n"
    assert payload.pages_json == {"pages": [{"page": 1, "markdown": "hello\n", "char_count": 6}]}
    assert payload.page_count == 1
    assert payload.char_count == 6
    assert payload.content_sha256 == hashlib.sha256(b"hello\n").hexdigest()


@pytest.mark.asyncio
async def test_persist_parse_text_artifacts_upserts_existing_row(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        parse_job, markdown_path = await _create_parse_job(session, tmp_path)
        first = await persist_parse_text_artifacts(
            session,
            parse_job=parse_job,
            markdown_path=markdown_path,
        )

        markdown_path.write_text("updated text\n", encoding="utf-8")
        markdown_path.with_suffix(".pages.json").write_text(
            '{"pages":[{"page":1,"markdown":"updated text\\n","char_count":13}]}',
            encoding="utf-8",
        )
        second = await persist_parse_text_artifacts(
            session,
            parse_job=parse_job,
            markdown_path=markdown_path,
        )
        row_count = await session.scalar(select(func.count()).select_from(DocumentParseTextRecord))

    assert first.id == second.id
    assert row_count == 1
    assert second.content_text == "updated text\n"
    assert second.char_count == 13
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_parse_texts_persists_missing_artifacts(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_parse_job(session, tmp_path, source_file_id="140120260515538453")
        existing_job, existing_markdown_path = await _create_parse_job(
            session,
            tmp_path,
            source_file_id="140120260515538454",
        )
        await persist_parse_text_artifacts(
            session,
            parse_job=existing_job,
            markdown_path=existing_markdown_path,
        )

        summary = await backfill_parse_texts(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            limit=10,
        )
        row_count = await session.scalar(select(func.count()).select_from(DocumentParseTextRecord))

    assert summary.total_pending == 1
    assert summary.candidates == 1
    assert summary.persisted == 1
    assert summary.failed == 0
    assert row_count == 2
    await engine.dispose()
