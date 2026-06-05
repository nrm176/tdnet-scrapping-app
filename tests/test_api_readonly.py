from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tdnet import api as tdnet_api
from tdnet.models import TdnetDisclosure
from tdnet.orm import (
    Base,
    DisclosureRecord,
    DocumentAnalysisResultRecord,
    DocumentParseJobRecord,
    ReportTagAssignmentRecord,
    ReportTagRecord,
)
from tdnet.repository import complete_disclosure_file, get_or_create_disclosure_file, upsert_disclosures, upsert_parse_text


@pytest.mark.asyncio
async def test_read_api_exposes_lineage_and_summaries(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
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

    async with session_factory() as session:
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
        text = "業績予想の修正に関するお知らせ\n今回修正予想 18,000 2,580\n"
        parse_job = DocumentParseJobRecord(
            file_id=file_record.id,
            parser_name="apple-vision-ocr",
            parser_version="ocr-version",
            parse_status="completed",
            text_path=str(tmp_path / "parsed.md"),
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        tag = ReportTagRecord(
            slug="forecast_revision",
            label_ja="業績予想・差異",
            label_en="Forecast revision or variance",
            description="Forecast revisions and differences between forecasts and results.",
            priority=30,
            active=True,
        )
        session.add_all([parse_job, tag])
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
        session.add_all(
            [
                ReportTagAssignmentRecord(
                    disclosure_id=disclosure.id,
                    tag_slug="forecast_revision",
                    file_id=file_record.id,
                    parse_job_id=parse_job.id,
                    is_primary=True,
                    confidence=0.95,
                    source="deterministic",
                    evidence_json={"title": ["業績予想"]},
                    tagger_name="tdnet-tagging",
                    tagger_version="test",
                ),
                DocumentAnalysisResultRecord(
                    file_id=file_record.id,
                    parse_job_id=parse_job.id,
                    analysis_type="financial_facts",
                    analyzer_name="tdnet-financial-facts",
                    analyzer_version="test",
                    status="completed",
                    result_json={"facts": [{"label": "sales", "value": "18,000"}]},
                    result_text="sales=18,000",
                ),
            ]
        )
        await session.commit()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    tdnet_api.app.dependency_overrides[tdnet_api.get_session] = override_get_session
    try:
        transport = ASGITransport(app=tdnet_api.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            lineage_response = await client.get(f"/disclosures/{disclosure.id}/lineage")
            parser_status_response = await client.get(f"/disclosures/{disclosure.id}/parser-status")
            timeline_response = await client.get("/companies/85600/timeline")
            parser_quality_response = await client.get("/parser-quality")
            tags_response = await client.get("/tags")
    finally:
        tdnet_api.app.dependency_overrides.clear()
        await engine.dispose()

    assert lineage_response.status_code == 200
    lineage = lineage_response.json()
    assert lineage["disclosure"]["id"] == disclosure.id
    assert lineage["tags"][0]["slug"] == "forecast_revision"
    assert lineage["tags"][0]["file_id"] == file_record.id
    assert lineage["files"][0]["parse_jobs"][0]["parse_text"]["char_count"] == len(text)
    assert lineage["files"][0]["analysis_results"][0]["analysis_type"] == "financial_facts"

    assert parser_status_response.status_code == 200
    parser_status = parser_status_response.json()
    assert parser_status["files"][0]["parse_jobs"][0]["parser_name"] == "apple-vision-ocr"

    assert timeline_response.status_code == 200
    timeline = timeline_response.json()
    assert timeline["total"] == 1
    assert timeline["disclosures"][0]["completed_file_count"] == 1
    assert timeline["disclosures"][0]["completed_parse_job_count"] == 1
    assert timeline["disclosures"][0]["analysis_result_count"] == 1

    assert parser_quality_response.status_code == 200
    parser_quality = parser_quality_response.json()
    assert parser_quality["total_jobs"] == 1
    assert parser_quality["completed_jobs"] == 1
    assert parser_quality["parse_texts"] == 1
    assert parser_quality["parsers"][0]["average_char_count"] == len(text)

    assert tags_response.status_code == 200
    tags = tags_response.json()
    assert tags["tags"][0]["slug"] == "forecast_revision"
    assert tags["tags"][0]["assignment_count"] == 1
