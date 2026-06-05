from __future__ import annotations

import hashlib
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tdnet.cli import _build_parser
from tdnet.financial_facts import (
    FINANCIAL_FACTS_ANALYSIS_TYPE,
    FINANCIAL_FACTS_ANALYZER_NAME,
    extract_financial_facts,
    analyze_financial_facts,
)
from tdnet.models import TdnetDisclosure
from tdnet.orm import Base, DisclosureRecord, DocumentAnalysisResultRecord, DocumentParseJobRecord
from tdnet.repository import complete_disclosure_file, get_or_create_disclosure_file, upsert_disclosures, upsert_parse_text


FINANCIAL_TEXT = """
| | 売上高 | 営業利益 | 経常利益 | 親会社株主に帰属する当期純利益 | 1株当たり当期純利益 |
| 今回修正予想(A) | 18,000 | 2,580 | 2,500 | 1,600 | 120.50 |
| 前回発表予想(B) | 16,500 | 2,100 | 2,050 | 1,300 | 98.00 |
| 増減額(A-B) | 1,500 | 480 | 450 | 300 | 22.50 |
| 増減率(%) | 9.1% | 22.9% | 22.0% | 23.1% | 23.0% |
1株当たり配当金 40.00 45.00
"""

TDNET_RESULT_TABLE_TEXT = """
（１）連結経営成績(累計) (％表示は、対前年同四半期増減率)

|Col1|売上高|Col3|営業利益|Col5|経常利益|Col7|親会社株主に帰属<br>する四半期純利益|Col9|
|---|---|---|---|---|---|---|---|---|
|2026年７月期第３四半期<br>2025年７月期第３四半期|百万円<br>31,107<br>28,441|％<br>9.4<br>13.3|百万円<br>7,016<br>6,647|％<br>5.6<br>23.6|百万円<br>7,178<br>6,621|％<br>8.4<br>21.7|百万円<br>4,208<br>3,770|％<br>11.6<br>7.9|

|Col1|１株当たり<br>四半期純利益|潜在株式調整後<br>１株当たり<br>四半期純利益|
|---|---|---|
|2026年７月期第３四半期<br>2025年７月期第３四半期|円<br>銭<br>13.26<br>11.82|円<br>銭<br>13.17<br>11.76|

|２．配当の状況|Col2|Col3|Col4|Col5|Col6|
|---|---|---|---|---|---|
||年間配当金|年間配当金|年間配当金|年間配当金|年間配当金|
||第１四半期末|第２四半期末|第３四半期末|期末|合計|
|2025年７月期<br>2026年７月期|円<br>銭<br>-<br>-|円<br>銭<br>0.00<br>0.00|円<br>銭<br>-<br>-|円<br>銭<br>8.00|円<br>銭<br>8.00|

以上の結果、当第3四半期連結累計期間における売上高は14,221百万円(前年同期比7.4%増)、営業利益は3,613百万円となりました。
3.セグメント利益は、四半期連結損益計算書の営業利益と調整を行っております。
"""

UNEVEN_TABLE_TEXT = """
|Col1|売上高|
|---|---|
|2026年７月期第３四半期<br>2025年７月期第３四半期|百万円<br>31,107<br>28,441|％<br>9.4<br>13.3|
"""


def test_extract_financial_facts_finds_metrics_and_forecast_rows():
    result = extract_financial_facts(
        title="業績予想及び配当予想の修正に関するお知らせ",
        content_text=FINANCIAL_TEXT,
        disclosure_id="abc",
        code="12345",
        disclosure_date=date(2026, 5, 1),
    )

    facts = result["facts"]
    metric_deltas = result["metric_deltas"]
    summary = result["summary"]
    forecast_rows = [fact for fact in facts if fact["type"] == "forecast_revision_row"]
    metric_rows = [fact for fact in facts if fact["type"] == "metric_row"]
    deltas_by_metric = {delta["metric"]: delta for delta in metric_deltas}

    assert summary["has_forecast_revision"] is True
    assert summary["metric_delta_count"] == 5
    assert set(summary["metric_keys"]) >= {"eps", "dividend_per_share"}
    assert len(forecast_rows) == 4
    assert forecast_rows[0]["values"][0]["metric"] == "net_sales"
    assert forecast_rows[0]["values"][0]["value"] == 18000
    assert forecast_rows[-1]["values"][0]["unit"] == "percent"
    assert deltas_by_metric["net_sales"]["comparison_basis"] == "previous_forecast"
    assert deltas_by_metric["net_sales"]["current_value"] == 18000
    assert deltas_by_metric["net_sales"]["comparison_value"] == 16500
    assert deltas_by_metric["net_sales"]["change_value"] == 1500
    assert deltas_by_metric["net_sales"]["reported_change_pct"] == 9.1
    assert deltas_by_metric["net_sales"]["computed_change_pct"] == pytest.approx(9.09)
    assert deltas_by_metric["net_sales"]["change_pct_source"] == "reported"
    assert any(fact["metric"] == "dividend_per_share" and fact["values"][0]["value"] == 40.0 for fact in metric_rows)
    assert result["document"]["code"] == "12345"


def test_extract_financial_facts_ignores_tdnet_table_scaffolding():
    result = extract_financial_facts(
        title="2026年７月期第３四半期決算短信〔日本基準〕(連結)",
        content_text=TDNET_RESULT_TABLE_TEXT,
        disclosure_id="abc",
        code="23530",
        disclosure_date=date(2026, 6, 5),
    )

    facts = result["facts"]
    metric_deltas = result["metric_deltas"]
    forecast_rows = [fact for fact in facts if fact["type"] == "forecast_revision_row"]
    metric_rows = {fact["metric"]: fact for fact in facts if fact["type"] == "metric_row"}
    deltas_by_metric = {delta["metric"]: delta for delta in metric_deltas}

    assert forecast_rows == []
    assert result["summary"]["has_forecast_revision"] is False
    assert result["summary"]["metric_delta_count"] == 5
    assert set(result["summary"]["metric_keys"]) >= {"eps", "net_income", "net_sales"}
    assert metric_rows["net_sales"]["values"] == [
        {"raw": "31,107", "value": 31107, "unit": None},
        {"raw": "28,441", "value": 28441, "unit": None},
    ]
    assert metric_rows["operating_profit"]["values"][0]["value"] == 7016
    assert metric_rows["ordinary_profit"]["values"][0]["value"] == 7178
    assert metric_rows["net_income"]["values"][0]["value"] == 4208
    assert metric_rows["eps"]["values"] == [
        {"raw": "13.26", "value": 13.26, "unit": None},
        {"raw": "11.82", "value": 11.82, "unit": None},
    ]
    assert deltas_by_metric["net_sales"]["period"] == "2026年7月期第3四半期"
    assert deltas_by_metric["net_sales"]["comparison_period"] == "2025年7月期第3四半期"
    assert deltas_by_metric["net_sales"]["comparison_basis"] == "prior_year_same_quarter"
    assert deltas_by_metric["net_sales"]["current_value"] == 31107
    assert deltas_by_metric["net_sales"]["comparison_value"] == 28441
    assert deltas_by_metric["net_sales"]["change_value"] == 2666
    assert deltas_by_metric["net_sales"]["reported_change_pct"] == 9.4
    assert deltas_by_metric["net_sales"]["computed_change_pct"] == pytest.approx(9.37)
    assert deltas_by_metric["net_sales"]["change_pct_source"] == "reported"
    assert deltas_by_metric["operating_profit"]["change_value"] == 369
    assert deltas_by_metric["operating_profit"]["reported_change_pct"] == 5.6
    assert deltas_by_metric["ordinary_profit"]["change_value"] == 557
    assert deltas_by_metric["ordinary_profit"]["reported_change_pct"] == 8.4
    assert deltas_by_metric["net_income"]["change_value"] == 438
    assert deltas_by_metric["net_income"]["reported_change_pct"] == 11.6
    assert deltas_by_metric["eps"]["change_value"] == pytest.approx(1.44)
    assert deltas_by_metric["eps"]["reported_change_pct"] is None
    assert deltas_by_metric["eps"]["computed_change_pct"] == pytest.approx(12.18)
    assert deltas_by_metric["eps"]["change_pct_source"] == "computed"
    assert all(value["raw"] not in {"1", "3", "5", "7", "9", "(1)"} for fact in facts for value in fact["values"])


def test_extract_financial_facts_handles_uneven_table_widths():
    result = extract_financial_facts(
        title="2026年７月期第３四半期決算短信〔日本基準〕(連結)",
        content_text=UNEVEN_TABLE_TEXT,
    )

    deltas_by_metric = {delta["metric"]: delta for delta in result["metric_deltas"]}

    assert deltas_by_metric["net_sales"]["current_value"] == 31107
    assert deltas_by_metric["net_sales"]["comparison_value"] == 28441
    assert deltas_by_metric["net_sales"]["change_value"] == 2666
    assert deltas_by_metric["net_sales"]["reported_change_pct"] is None
    assert deltas_by_metric["net_sales"]["change_pct_source"] == "computed"


@pytest.mark.asyncio
async def test_analyze_financial_facts_persists_analysis_result_and_skips_completed(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        disclosure = TdnetDisclosure(
            time="15:30",
            code="12345",
            name="テスト株式会社",
            title="業績予想及び配当予想の修正に関するお知らせ",
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
        await upsert_parse_text(
            session,
            parse_job=parse_job,
            content_text=FINANCIAL_TEXT,
            pages_json={"pages": [{"page": 1, "markdown": FINANCIAL_TEXT, "char_count": len(FINANCIAL_TEXT)}]},
            page_count=1,
            char_count=len(FINANCIAL_TEXT),
            content_sha256=hashlib.sha256(FINANCIAL_TEXT.encode("utf-8")).hexdigest(),
        )

        summary = await analyze_financial_facts(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            analyzer_version="test-analyzer-v2",
            limit=10,
        )
        second_summary = await analyze_financial_facts(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            analyzer_version="test-analyzer-v2",
            limit=10,
        )
        result = await session.scalar(select(DocumentAnalysisResultRecord))
        assert result is not None
        result.status = "failed"
        result.last_analysis_error = "retry me"
        await session.commit()
        blocked_summary = await analyze_financial_facts(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            analyzer_version="test-analyzer-v2",
            limit=10,
        )
        retry_summary = await analyze_financial_facts(
            session,
            parser_name="pymupdf4llm",
            parser_version="test-version",
            analyzer_version="test-analyzer-v2",
            retry_failed=True,
            limit=10,
        )
        await session.refresh(result)

    assert summary.analyzed == 1
    assert summary.failed == 0
    assert second_summary.candidates == 0
    assert blocked_summary.candidates == 0
    assert retry_summary.analyzed == 1
    assert result.file_id == file_record.id
    assert result.parse_job_id == parse_job.id
    assert result.analysis_type == FINANCIAL_FACTS_ANALYSIS_TYPE
    assert result.analyzer_name == FINANCIAL_FACTS_ANALYZER_NAME
    assert result.analyzer_version == "test-analyzer-v2"
    assert result.status == "completed"
    assert result.result_json is not None
    assert result.result_json["analyzer_version"] == "test-analyzer-v2"
    assert result.result_json["summary"]["has_forecast_revision"] is True
    assert "financial_facts fact_count=" in (result.result_text or "")
    await engine.dispose()


def test_cli_analyze_financials_command_parses():
    parser = _build_parser()

    args = parser.parse_args(
        [
            "analyze-financials",
            "--limit",
            "5",
            "--from",
            "2026-05-01",
            "--code",
            "12345",
            "--retry-failed",
            "--json",
        ]
    )

    assert args.command == "analyze-financials"
    assert args.limit == 5
    assert args.date_from == date(2026, 5, 1)
    assert args.code == "12345"
    assert args.retry_failed is True
    assert args.json is True
