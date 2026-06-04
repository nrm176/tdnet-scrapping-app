from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.orm import Base, DisclosureFileRecord, DisclosureRecord, DocumentParseJobRecord
from tdnet.review import ParsedPage, build_parse_review_report, score_pages


def test_score_pages_flags_sparse_extraction():
    score, warnings = score_pages(
        [
            ParsedPage(page=1, markdown="", char_count=0),
            ParsedPage(page=2, markdown="short", char_count=5),
        ],
        file_size_bytes=3_000_000,
    )

    assert score >= 70
    assert "very low total text" in warnings
    assert "large PDF with sparse extracted text" in warnings


def test_score_pages_flags_dense_garbled_extraction():
    garbled = ("\x01\x02\x03J®XG}uvªLMc\x89§\x9a<br>" * 35) + "|<br>|<br>|<br>|"

    score, warnings = score_pages(
        [ParsedPage(page=1, markdown=garbled, char_count=len(garbled))],
        file_size_bytes=152_301,
    )

    assert score >= 40
    assert "high control-character ratio with no Japanese text" in warnings


def test_score_pages_does_not_flag_plain_ascii_text_as_garbled():
    text = "Revenue forecast update with normal extractable ASCII text. " * 20

    score, warnings = score_pages(
        [ParsedPage(page=1, markdown=text, char_count=len(text))],
        file_size_bytes=152_301,
    )

    assert score == 0
    assert warnings == ("looks normal",)


@pytest.mark.asyncio
async def test_build_parse_review_report(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    md_path = tmp_path / "parsed" / "pymupdf4llm.test-version.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("parsed markdown", encoding="utf-8")
    md_path.with_name("pymupdf4llm.test-version.pages.json").write_text(
        '{"pages":[{"page":1,"markdown":"parsed markdown","char_count":15}]}',
        encoding="utf-8",
    )
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def fake_render(pdf_path: Path, page_number: int, destination: Path, *, zoom: float = 1.5) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"png")

    monkeypatch.setattr("tdnet.review.render_pdf_page", fake_render)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        disclosure = DisclosureRecord(
            id="abc123def4567890",
            disclosure_date=date(2026, 5, 16),
            time="15:30",
            code="12345",
            name="テスト株式会社",
            title="業績予想の修正に関するお知らせ",
            pdf_url="https://www.release.tdnet.info/inbs/test.pdf",
            xbrl_available=False,
            place="東",
            history="",
        )
        session.add(disclosure)
        await session.flush()
        file_record = DisclosureFileRecord(
            disclosure_id=disclosure.id,
            file_type="pdf",
            source_url=disclosure.pdf_url,
            source_file_id="test",
            storage_bucket="tdnet",
            storage_path=str(pdf_path),
            file_size_bytes=1000,
            download_status="completed",
        )
        session.add(file_record)
        await session.flush()
        session.add(
            DocumentParseJobRecord(
                file_id=file_record.id,
                parser_name="pymupdf4llm",
                parser_version="test-version",
                parse_status="completed",
                text_path=str(md_path),
                text_sha256="a" * 64,
            )
        )
        await session.commit()

        report = await build_parse_review_report(
            session,
            output_root=tmp_path / "reviews",
            parser_version="test-version",
            limit=1,
        )

    html = report.index_path.read_text(encoding="utf-8")
    assert report.reviewed_count == 1
    assert "TDnet Parse Review" in html
    assert "業績予想の修正に関するお知らせ" in html
    assert "parsed markdown" in html
    assert list((report.output_dir / "assets").glob("*.png"))

    await engine.dispose()
