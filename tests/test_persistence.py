from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.models import TdnetDisclosure
from tdnet.orm import Base
from tdnet.repository import (
    count_disclosures,
    get_disclosure,
    get_latest_disclosure_date,
    query_disclosures,
    upsert_disclosures,
)


@pytest.mark.asyncio
async def test_upsert_and_query_disclosures():
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
    later_disclosure = TdnetDisclosure(
        time="16:00",
        code="67890",
        name="後続株式会社",
        title="配当予想の修正に関するお知らせ",
        pdf_url="https://www.release.tdnet.info/inbs/140120251022001001.pdf",
        xbrl_available=False,
        place="東",
        history="",
        disclosure_date=date(2025, 10, 22),
    )

    async with session_factory() as session:
        assert await get_latest_disclosure_date(session) is None
        persisted_count = await upsert_disclosures(session, [disclosure, later_disclosure])
        assert persisted_count == 2

    async with session_factory() as session:
        assert await count_disclosures(session, disclosure_date=date(2025, 10, 21)) == 1
        assert await get_latest_disclosure_date(session) == date(2025, 10, 22)
        results = await query_disclosures(session, disclosure_date=date(2025, 10, 21))
        assert len(results) == 1
        assert results[0].id == disclosure.id

        found = await get_disclosure(session, disclosure.id)
        assert found is not None
        assert found.title == disclosure.title

    await engine.dispose()
