from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.models import TdnetDisclosure
from tdnet.ocr import (
    APPLE_VISION_OCR_NAME,
    OcrArtifacts,
    needs_ocr,
    ocr_pending_files,
    select_ocr_candidates,
)
from tdnet.orm import Base, DisclosureRecord, DocumentParseJobRecord, DocumentParseTextRecord
from tdnet.repository import (
    complete_disclosure_file,
    get_or_create_disclosure_file,
    upsert_disclosures,
)
from tdnet.review import ParsedPage


async def _create_completed_pdf_with_parse(
    session,
    tmp_path: Path,
    *,
    source_parser_version: str = "source-version",
    page_markdown: str = "",
    page_char_count: int = 0,
) -> int:
    disclosure = TdnetDisclosure(
        time="15:30",
        code="85600",
        name="宮崎太銀",
        title="業績予想の修正に関するお知らせ",
        pdf_url="https://www.release.tdnet.info/inbs/140120260414503449.pdf",
        xbrl_available=False,
        place="福",
        history="",
        disclosure_date=date(2026, 4, 20),
    )
    await upsert_disclosures(session, [disclosure])
    record = await session.get(DisclosureRecord, disclosure.id)
    assert record is not None

    pdf_path = tmp_path / "140120260414503449.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    file_record = await get_or_create_disclosure_file(
        session,
        disclosure=record,
        file_type="pdf",
        source_url=str(disclosure.pdf_url),
        source_file_id="140120260414503449",
        storage_bucket="tdnet-forecast-correction",
        storage_path=str(pdf_path),
    )
    await complete_disclosure_file(
        session,
        file_record,
        file_size_bytes=500_000,
        sha256="a" * 64,
        content_type="application/pdf",
    )

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    markdown_path = parsed_dir / f"pymupdf4llm.{source_parser_version}.md"
    markdown_path.write_text(page_markdown, encoding="utf-8")
    markdown_path.with_name(f"pymupdf4llm.{source_parser_version}.pages.json").write_text(
        json.dumps(
            {"pages": [{"page": 1, "markdown": page_markdown, "char_count": page_char_count}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session.add(
        DocumentParseJobRecord(
            file_id=file_record.id,
            parser_name="pymupdf4llm",
            parser_version=source_parser_version,
            parse_status="completed",
            text_path=str(markdown_path),
            text_sha256="b" * 64,
        )
    )
    await session.commit()
    return file_record.id


def test_needs_ocr_flags_sparse_pages():
    should_ocr, score, warnings = needs_ocr(
        [ParsedPage(page=1, markdown="", char_count=0)],
        file_size_bytes=500_000,
    )

    assert should_ocr is True
    assert score >= 40
    assert "very low total text" in warnings


def test_needs_ocr_ignores_dense_pages():
    should_ocr, score, warnings = needs_ocr(
        [ParsedPage(page=1, markdown="x" * 1000, char_count=1000)],
        file_size_bytes=500_000,
    )

    assert should_ocr is False
    assert score == 0
    assert warnings == ("looks normal",)


@dataclass
class FakeOcrProvider:
    name: str = APPLE_VISION_OCR_NAME
    version: str = "ocr-version"

    def is_available(self) -> bool:
        return True

    def ocr_pdf(self, pdf_path: Path) -> OcrArtifacts:
        markdown_path = pdf_path.parent / "parsed" / f"{self.name}.{self.version}.md"
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text("<!-- page: 1 -->\n\nOCR text\n", encoding="utf-8")
        pages_path = markdown_path.with_name(f"{self.name}.{self.version}.pages.json")
        pages_path.write_text(
            '{"pages":[{"page":1,"markdown":"OCR text\\n","char_count":9}]}',
            encoding="utf-8",
        )
        metadata_path = markdown_path.with_name(f"{self.name}.{self.version}.meta.json")
        metadata_path.write_text("{}", encoding="utf-8")
        return OcrArtifacts(
            markdown_path=markdown_path,
            pages_path=pages_path,
            metadata_path=metadata_path,
            markdown_sha256="c" * 64,
            page_count=1,
            char_count=25,
        )


@pytest.mark.asyncio
async def test_select_ocr_candidates_uses_low_text_source_parse(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        file_id = await _create_completed_pdf_with_parse(session, tmp_path)
        candidates = await select_ocr_candidates(
            session,
            source_parser_version="source-version",
            ocr_parser_version="ocr-version",
        )

    assert [candidate.file_record.id for candidate in candidates] == [file_id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_ocr_pending_files_marks_completed(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_completed_pdf_with_parse(session, tmp_path)
        summary = await ocr_pending_files(
            session,
            source_parser_version="source-version",
            parser_version="ocr-version",
            provider=FakeOcrProvider(),
        )
        job = await session.scalar(
            select(DocumentParseJobRecord).where(
                DocumentParseJobRecord.parser_name == APPLE_VISION_OCR_NAME
            )
        )
        parse_text = await session.scalar(select(DocumentParseTextRecord))

    assert summary.total_pending == 1
    assert summary.ocr_completed == 1
    assert summary.failed == 0
    assert job is not None
    assert job.parse_status == "completed"
    assert job.text_path is not None
    assert job.text_sha256 == "c" * 64
    assert parse_text is not None
    assert parse_text.parse_job_id == job.id
    assert parse_text.content_text == "<!-- page: 1 -->\n\nOCR text\n"
    assert parse_text.pages_json is not None
    assert parse_text.page_count == 1
    assert parse_text.char_count == 27
    await engine.dispose()
