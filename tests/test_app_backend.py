from __future__ import annotations

import hashlib
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.backend.services.search_service import (
    get_parse_job_detail,
    list_parser_options,
    list_report_calendar_days,
    list_report_tags,
    search_parse_texts,
)
from tdnet.models import TdnetDisclosure
from tdnet.ixbrl_text import IXBRL_TEXT_PARSER_NAME
from tdnet.orm import Base, DisclosureRecord, DocumentParseJobRecord
from tdnet.parsers import PARSER_NAME
from tdnet.repository import complete_disclosure_file, get_or_create_disclosure_file, upsert_disclosures, upsert_parse_text
from tdnet.tagging import tag_reports


@pytest.mark.asyncio
async def test_search_parse_texts_finds_japanese_body_text(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
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
        disclosure_record = await session.get(DisclosureRecord, disclosure.id)
        assert disclosure_record is not None
        pdf_path = tmp_path / "140120260414503449.pdf"
        pdf_path.write_bytes(b"%PDF-1.7\n")
        file_record = await get_or_create_disclosure_file(
            session,
            disclosure=disclosure_record,
            file_type="pdf",
            source_url=str(disclosure.pdf_url),
            source_file_id="140120260414503449",
            storage_bucket="tdnet-forecast-correction",
            storage_path=str(pdf_path),
        )
        await complete_disclosure_file(
            session,
            file_record,
            file_size_bytes=10,
            sha256="a" * 64,
            content_type="application/pdf",
        )
        parse_job = DocumentParseJobRecord(
            file_id=file_record.id,
            parser_name="apple-vision-ocr",
            parser_version="ocr-version",
            parse_status="completed",
            text_path=str(tmp_path / "parsed.md"),
            text_sha256="b" * 64,
        )
        session.add(parse_job)
        await session.commit()
        await session.refresh(parse_job)
        text = "業績予想の修正に関するお知らせ\n今回修正予想 18,000 2,580\n"
        await upsert_parse_text(
            session,
            parse_job=parse_job,
            content_text=text,
            pages_json={"pages": [{"page": 1, "markdown": text, "char_count": len(text)}]},
            page_count=1,
            char_count=len(text),
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        await tag_reports(
            session,
            parser_name="apple-vision-ocr",
            parser_version="ocr-version",
            limit=10,
        )

        options = await list_parser_options(session)
        response = await search_parse_texts(session, query="18,000", parser_name="apple-vision-ocr")
        title_response = await search_parse_texts(
            session,
            title_query="業績予想",
            parser_name="apple-vision-ocr",
        )
        text_response = await search_parse_texts(
            session,
            text_query="18,000",
            parser_name="apple-vision-ocr",
        )
        title_miss_response = await search_parse_texts(
            session,
            title_query="18,000",
            parser_name="apple-vision-ocr",
        )
        combined_response = await search_parse_texts(
            session,
            title_query="業績予想",
            text_query="18,000",
            parser_name="apple-vision-ocr",
        )
        tagged_response = await search_parse_texts(
            session,
            parser_name="apple-vision-ocr",
            tags=["forecast_revision"],
        )
        missing_tag_response = await search_parse_texts(
            session,
            parser_name="apple-vision-ocr",
            tags=["share_buyback"],
        )
        calendar_days = await list_report_calendar_days(
            session,
            month_start=date(2026, 4, 1),
            month_end=date(2026, 4, 30),
            parser_name="apple-vision-ocr",
            tags=["forecast_revision"],
        )
        tag_options = await list_report_tags(session)
        detail = await get_parse_job_detail(session, parse_job.id)

    assert options[0].parser_name == "apple-vision-ocr"
    assert options[0].parse_texts == 1
    assert response.total == 1
    assert response.results[0].code == "85600"
    assert "18,000" in response.results[0].snippet
    assert title_response.total == 1
    assert text_response.total == 1
    assert title_miss_response.total == 0
    assert combined_response.total == 1
    assert tagged_response.total == 1
    assert tagged_response.results[0].tags[0].slug == "forecast_revision"
    assert missing_tag_response.total == 0
    assert calendar_days[0].record_count == 1
    assert calendar_days[0].report_count == 1
    assert any(tag.slug == "forecast_revision" and tag.assignment_count == 1 for tag in tag_options)
    assert detail is not None
    assert detail.tags[0].slug == "forecast_revision"
    assert detail.pages[0].page == 1
    assert detail.content_text == text
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_parse_texts_prefers_best_parser_by_default(tmp_path):
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
        file_record = await get_or_create_disclosure_file(
            session,
            disclosure=disclosure_record,
            file_type="pdf",
            source_url=str(disclosure.pdf_url),
            source_file_id="140120260601558048",
            storage_bucket="tdnet-forecast-correction",
            storage_path=str(pdf_path),
        )
        await complete_disclosure_file(
            session,
            file_record,
            file_size_bytes=10,
            sha256="a" * 64,
            content_type="application/pdf",
        )

        for parser_name, parser_version, text in [
            (PARSER_NAME, "source-version", "garbled-token \x01\x02\x03J®XG}uvªLMc\x89§\x9a\n"),
            (IXBRL_TEXT_PARSER_NAME, "ixbrl-version", "通期業績予想の修正に関するお知らせ\n今回修正予想 28,000\n"),
        ]:
            parse_job = DocumentParseJobRecord(
                file_id=file_record.id,
                parser_name=parser_name,
                parser_version=parser_version,
                parse_status="completed",
                text_path=str(tmp_path / f"{parser_name}.md"),
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
            session.add(parse_job)
            await session.commit()
            await session.refresh(parse_job)
            await upsert_parse_text(
                session,
                parse_job=parse_job,
                content_text=text,
                pages_json={"pages": [{"page": 1, "markdown": text, "char_count": len(text)}]},
                page_count=1,
                char_count=len(text),
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        default_response = await search_parse_texts(session)
        clean_text_response = await search_parse_texts(session, text_query="28,000")
        ignored_garbled_response = await search_parse_texts(session, text_query="garbled-token")
        explicit_pymupdf_response = await search_parse_texts(
            session,
            text_query="garbled-token",
            parser_name=PARSER_NAME,
        )

    assert default_response.total == 1
    assert default_response.results[0].parser_name == IXBRL_TEXT_PARSER_NAME
    assert clean_text_response.total == 1
    assert clean_text_response.results[0].parser_name == IXBRL_TEXT_PARSER_NAME
    assert ignored_garbled_response.total == 0
    assert explicit_pymupdf_response.total == 1
    assert explicit_pymupdf_response.results[0].parser_name == PARSER_NAME
    await engine.dispose()
