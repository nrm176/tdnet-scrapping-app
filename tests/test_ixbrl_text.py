from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.ixbrl_text import (
    IXBRL_TEXT_PARSER_NAME,
    extract_ixbrl_text,
    parse_ixbrl_text_pending,
)
from tdnet.models import TdnetDisclosure
from tdnet.orm import Base, DisclosureRecord, DocumentParseJobRecord, DocumentParseTextRecord
from tdnet.parsers import PARSER_NAME
from tdnet.repository import (
    complete_disclosure_file,
    get_or_create_disclosure_file,
    upsert_disclosures,
    upsert_parse_text,
)


def _write_ixbrl_zip(path: Path) -> None:
    html = """<!doctype html>
    <html>
      <head><title>通期業績予想の修正に関するお知らせ</title><style>.x{}</style></head>
      <body>
        <div>
          <ix:nonNumeric name="tse-ed-t:Title">通期業績予想の修正に関するお知らせ</ix:nonNumeric>
          <p>今回修正予想 28,000 2,680</p>
        </div>
        <ix:header>
          <ix:hidden><ix:nonNumeric name="hidden">非表示メタデータ</ix:nonNumeric></ix:hidden>
          <ix:resources><xbrli:context>40260</xbrli:context></ix:resources>
        </ix:header>
      </body>
    </html>
    """
    with ZipFile(path, "w") as zip_file:
        zip_file.writestr("tse-rvfc-40260-20260601558048-ixbrl.htm", html.encode("utf-8"))


def test_extract_ixbrl_text_uses_visible_html_text(tmp_path):
    zip_path = tmp_path / "ixbrl.zip"
    _write_ixbrl_zip(zip_path)

    text, html_entry = extract_ixbrl_text(zip_path)

    assert html_entry.endswith("-ixbrl.htm")
    assert "通期業績予想の修正に関するお知らせ" in text
    assert "今回修正予想 28,000 2,680" in text
    assert "非表示メタデータ" not in text
    assert "40260" not in text


@pytest.mark.asyncio
async def test_parse_ixbrl_text_pending_persists_fallback_on_pdf_file(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        disclosure = TdnetDisclosure(
            time="14:30",
            code="40260",
            name="神島化学工業",
            title="通期業績予想の修正に関するお知らせ",
            pdf_url="https://www.release.tdnet.info/inbs/140120260601558048.pdf",
            xbrl_available=True,
            xbrl_url="https://www.release.tdnet.info/inbs/091220260601558048.zip",
            place="東",
            history="",
            disclosure_date=date(2026, 6, 3),
        )
        await upsert_disclosures(session, [disclosure])
        disclosure_record = await session.get(DisclosureRecord, disclosure.id)
        assert disclosure_record is not None

        pdf_path = tmp_path / "140120260601558048.pdf"
        pdf_path.write_bytes(b"%PDF-1.7\n")
        xbrl_path = tmp_path / "091220260601558048.zip"
        _write_ixbrl_zip(xbrl_path)
        pdf_file = await get_or_create_disclosure_file(
            session,
            disclosure=disclosure_record,
            file_type="pdf",
            source_url=str(disclosure.pdf_url),
            source_file_id="140120260601558048",
            storage_bucket="tdnet-forecast-correction",
            storage_path=str(pdf_path),
        )
        xbrl_file = await get_or_create_disclosure_file(
            session,
            disclosure=disclosure_record,
            file_type="xbrl",
            source_url=str(disclosure.xbrl_url),
            source_file_id="091220260601558048",
            storage_bucket="tdnet-forecast-correction",
            storage_path=str(xbrl_path),
        )
        await complete_disclosure_file(
            session,
            pdf_file,
            file_size_bytes=152_301,
            sha256="a" * 64,
            content_type="application/pdf",
        )
        await complete_disclosure_file(
            session,
            xbrl_file,
            file_size_bytes=xbrl_path.stat().st_size,
            sha256="b" * 64,
            content_type="application/zip",
        )
        source_text = ("\x01\x02\x03J®XG}uvªLMc\x89§\x9a<br>" * 35) + "|<br>|<br>|"
        source_job = DocumentParseJobRecord(
            file_id=pdf_file.id,
            parser_name=PARSER_NAME,
            parser_version="source-version",
            parse_status="completed",
            text_path=str(tmp_path / "pymupdf.md"),
            text_sha256="c" * 64,
        )
        session.add(source_job)
        await session.commit()
        await session.refresh(source_job)
        await upsert_parse_text(
            session,
            parse_job=source_job,
            content_text=source_text,
            pages_json={"pages": [{"page": 1, "markdown": source_text, "char_count": len(source_text)}]},
            page_count=1,
            char_count=len(source_text),
            content_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        )

        summary = await parse_ixbrl_text_pending(
            session,
            source_parser_version="source-version",
            parser_version="ixbrl-version",
            limit=10,
        )
        ixbrl_job = await session.scalar(
            select(DocumentParseJobRecord).where(DocumentParseJobRecord.parser_name == IXBRL_TEXT_PARSER_NAME)
        )
        ixbrl_text = await session.scalar(
            select(DocumentParseTextRecord)
            .join(DocumentParseJobRecord)
            .where(DocumentParseJobRecord.parser_name == IXBRL_TEXT_PARSER_NAME)
        )

    assert summary.total_pending == 1
    assert summary.candidates == 1
    assert summary.parsed == 1
    assert summary.failed == 0
    assert ixbrl_job is not None
    assert ixbrl_job.file_id == pdf_file.id
    assert ixbrl_job.parse_status == "completed"
    assert ixbrl_job.text_path is not None
    assert "tdnet-ixbrl-text.ixbrl-version.md" in ixbrl_job.text_path
    assert ixbrl_text is not None
    assert "通期業績予想の修正に関するお知らせ" in ixbrl_text.content_text
    assert "今回修正予想 28,000 2,680" in ixbrl_text.content_text
    await engine.dispose()
