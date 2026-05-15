from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.artifacts import (
    FORECAST_CORRECTION_BUCKET,
    build_storage_path,
    is_forecast_correction,
)
from tdnet.models import TdnetDisclosure
from tdnet.orm import Base, DisclosureRecord
from tdnet.repository import (
    complete_disclosure_file,
    count_disclosure_files,
    fail_disclosure_file,
    get_or_create_disclosure_file,
    iter_disclosures_for_download,
    query_disclosure_files,
    upsert_disclosures,
)


def test_forecast_correction_detection():
    assert is_forecast_correction("業績予想の修正に関するお知らせ")
    assert is_forecast_correction("予想値と実績値との差異に関するお知らせ")
    assert not is_forecast_correction("剰余金の配当に関するお知らせ")


def test_build_storage_path_uses_forecast_bucket():
    disclosure = DisclosureRecord(
        id="abc123def4567890",
        disclosure_date=date(2026, 5, 15),
        time="15:30",
        code="12345",
        name="テスト株式会社",
        title="業績予想の修正に関するお知らせ",
        pdf_url="https://www.release.tdnet.info/inbs/140120260515999999.pdf",
        xbrl_available=False,
        place="東",
        history="",
    )

    bucket, path = build_storage_path(
        Path("/tmp/downloads"),
        disclosure,
        file_type="pdf",
        source_url=disclosure.pdf_url,
    )

    assert bucket == FORECAST_CORRECTION_BUCKET
    assert path == Path(
        "/tmp/downloads/tdnet-forecast-correction/"
        "140120260515999999/140120260515999999.pdf"
    )


@pytest.mark.asyncio
async def test_disclosure_file_lifecycle():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
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

    async with session_factory() as session:
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
            storage_path="/tmp/test.pdf",
        )
        assert file_record.download_status == "pending"

        await complete_disclosure_file(
            session,
            file_record,
            file_size_bytes=10,
            sha256="a" * 64,
            content_type="application/pdf",
        )

        assert await count_disclosure_files(session, download_status="completed") == 1
        files = await query_disclosure_files(session, disclosure_id=disclosure.id)
        assert len(files) == 1
        assert files[0].sha256 == "a" * 64

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_downloads_are_only_selected_for_retry():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    disclosure = TdnetDisclosure(
        time="15:30",
        code="12345",
        name="テスト株式会社",
        title="通常のお知らせ",
        pdf_url="https://www.release.tdnet.info/inbs/140120251021001001.pdf",
        xbrl_available=False,
        place="東",
        history="",
        disclosure_date=date(2025, 10, 21),
    )

    async with session_factory() as session:
        await upsert_disclosures(session, [disclosure])
        record = await session.get(DisclosureRecord, disclosure.id)
        assert record is not None
        file_record = await get_or_create_disclosure_file(
            session,
            disclosure=record,
            file_type="pdf",
            source_url=str(disclosure.pdf_url),
            source_file_id="140120251021001001",
            storage_bucket="tdnet",
            storage_path="/tmp/test.pdf",
        )
        await fail_disclosure_file(session, file_record, "404")

        assert await iter_disclosures_for_download(session) == []
        retry_candidates = await iter_disclosures_for_download(session, retry_failed=True)
        assert [candidate.id for candidate in retry_candidates] == [disclosure.id]

    await engine.dispose()
