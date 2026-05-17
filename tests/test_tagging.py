from __future__ import annotations

import hashlib
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.cli import _build_parser
from tdnet.models import TdnetDisclosure
from tdnet.orm import Base, DisclosureRecord, DocumentParseJobRecord
from tdnet.repository import complete_disclosure_file, get_or_create_disclosure_file, upsert_disclosures, upsert_parse_text
from tdnet.tagging import (
    classify_report,
    list_report_tag_summaries,
    list_tag_assignments_for_disclosures,
    normalize_tag_text,
    tag_reports,
)


def test_normalize_tag_text_handles_full_width_ascii():
    assert normalize_tag_text("ＥＴＦの収益分配") == "etfの収益分配"


def test_classify_report_prefers_specific_primary_tag():
    classification = classify_report("業績予想の修正及び配当予想の修正（増配）に関するお知らせ")
    slugs = [assignment.slug for assignment in classification.assignments]

    assert classification.primary_tag == "forecast_revision"
    assert "forecast_revision" in slugs
    assert "dividend_distribution" in slugs
    assert "correction_change" in slugs


def test_classify_report_uses_other_fallback():
    classification = classify_report("お知らせ")

    assert classification.primary_tag == "other"
    assert classification.assignments[0].slug == "other"
    assert classification.assignments[0].is_primary is True


@pytest.mark.asyncio
async def test_tag_reports_persists_assignments_and_counts(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        disclosure = TdnetDisclosure(
            time="15:30",
            code="12345",
            name="テスト株式会社",
            title="重要なお知らせ",
            pdf_url="https://www.release.tdnet.info/inbs/140120260501000001.pdf",
            xbrl_available=False,
            place="東",
            history="",
            disclosure_date=date(2026, 5, 1),
        )
        await upsert_disclosures(session, [disclosure])
        disclosure_record = await session.get(DisclosureRecord, disclosure.id)
        assert disclosure_record is not None
        pdf_path = tmp_path / "140120260501000001.pdf"
        pdf_path.write_bytes(b"%PDF-1.7\n")
        file_record = await get_or_create_disclosure_file(
            session,
            disclosure=disclosure_record,
            file_type="pdf",
            source_url=str(disclosure.pdf_url),
            source_file_id="140120260501000001",
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
        parse_job = DocumentParseJobRecord(
            file_id=file_record.id,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            parse_status="completed",
            text_path=str(tmp_path / "parsed.md"),
            text_sha256="b" * 64,
        )
        session.add(parse_job)
        await session.commit()
        await session.refresh(parse_job)
        content_text = "財務報告に係る内部統制の開示すべき重要な不備に関するお知らせ"
        await upsert_parse_text(
            session,
            parse_job=parse_job,
            content_text=content_text,
            pages_json={"pages": [{"page": 1, "markdown": content_text, "char_count": len(content_text)}]},
            page_count=1,
            char_count=len(content_text),
            content_sha256=hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
        )

        summary = await tag_reports(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            limit=10,
        )
        second_summary = await tag_reports(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            limit=10,
        )
        assignments = await list_tag_assignments_for_disclosures(session, [disclosure.id])
        tag_summaries = await list_report_tag_summaries(session)

    assert summary.tagged == 1
    assert second_summary.candidates == 0
    assert assignments[disclosure.id][0].slug == "audit_internal_control"
    assert assignments[disclosure.id][0].source == "content"
    assert any(tag.slug == "audit_internal_control" and tag.assignment_count == 1 for tag in tag_summaries)
    await engine.dispose()


def test_cli_tag_commands_parse():
    parser = _build_parser()

    tag_args = parser.parse_args(["tag-reports", "--limit", "5", "--from", "2026-05-01", "--json"])
    list_args = parser.parse_args(["list-tags", "--counts"])

    assert tag_args.command == "tag-reports"
    assert tag_args.limit == 5
    assert tag_args.json is True
    assert list_args.command == "list-tags"
    assert list_args.counts is True
