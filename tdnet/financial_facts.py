"""Deterministic financial fact extraction for parsed TDnet disclosures."""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Sequence

from sqlalchemy import and_, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from .orm import (
    DisclosureFileRecord,
    DisclosureRecord,
    DocumentAnalysisResultRecord,
    DocumentParseJobRecord,
    DocumentParseTextRecord,
)
from .parsers import PARSER_NAME, get_parser_version

FINANCIAL_FACTS_ANALYSIS_TYPE = "financial_facts"
FINANCIAL_FACTS_ANALYZER_NAME = "tdnet-deterministic-financial-facts"
FINANCIAL_FACTS_ANALYZER_VERSION = "2"

NUMBER_RE = re.compile(r"[△▲-]?\(?\d[\d,]*(?:\.\d+)?\)?%?")
TABLE_COLUMN_MARKER_RE = re.compile(r"\bCol\d+\b")


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label_ja: str
    unit: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class FinancialFactCandidate:
    disclosure: DisclosureRecord
    file_record: DisclosureFileRecord
    parse_job: DocumentParseJobRecord
    parse_text: DocumentParseTextRecord


@dataclass(frozen=True)
class FinancialFactAnalysisSummary:
    total_pending: int = 0
    candidates: int = 0
    analyzed: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0
    fact_counts: dict[str, int] = field(default_factory=dict)


METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        "eps",
        "1株当たり当期純利益",
        "jpy_per_share",
        (r"1\s*株\s*当たり.{0,12}(?:当期|四半期)?純利益", r"\bEPS\b"),
    ),
    MetricDefinition(
        "dividend_per_share",
        "1株当たり配当金",
        "jpy_per_share",
        (r"1\s*株\s*当たり.{0,8}配当", r"年間配当金", r"配当金", r"期末配当"),
    ),
    MetricDefinition(
        "net_sales",
        "売上高",
        "million_jpy",
        (r"売上高", r"売上収益", r"営業収益", r"経常収益"),
    ),
    MetricDefinition(
        "operating_profit",
        "営業利益",
        "million_jpy",
        (r"営業利益", r"営業損失"),
    ),
    MetricDefinition(
        "ordinary_profit",
        "経常利益",
        "million_jpy",
        (r"経常利益", r"経常損失"),
    ),
    MetricDefinition(
        "net_income",
        "純利益",
        "million_jpy",
        (
            r"親会社株主に帰属する.{0,8}(?:当期|四半期)?純利益",
            r"(?:当期|四半期)?純利益",
            r"純損失",
        ),
    ),
)

FORECAST_ROW_PATTERNS: tuple[tuple[str, str], ...] = (
    ("current_forecast", r"今回(?:修正)?予想|修正予想|今回発表予想"),
    ("previous_forecast", r"前回(?:発表)?予想|前回予想"),
    ("actual_result", r"実績|前期実績"),
    ("change_amount", r"増減額|修正額|差異額"),
    ("change_percent", r"増減率|修正率|差異率|増減%|増減％"),
)


def normalize_financial_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return normalized.replace("▲", "△").replace("−", "-").replace("ー", "-")


def _clean_line(value: str) -> str:
    line = normalize_financial_text(value)
    line = re.sub(r"<!--.*?-->", " ", line)
    line = re.sub(r"[`*_#]", "", line)
    line = line.replace("\\", " ")
    line = re.sub(r"\s+", " ", line)
    return line.strip(" |")


def _line_iter(content_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in content_text.splitlines():
        line = _clean_line(raw_line)
        if line:
            lines.append(line)
    return lines


def _matches_any(patterns: Sequence[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _first_match_position(patterns: Sequence[str], text: str) -> int | None:
    positions = [
        match.start()
        for pattern in patterns
        if (match := re.search(pattern, text, flags=re.IGNORECASE)) is not None
    ]
    return min(positions) if positions else None


def _metrics_in_line(line: str) -> list[MetricDefinition]:
    if re.search(r"1\s*株", line):
        per_share_metrics = [
            metric
            for metric in METRIC_DEFINITIONS
            if metric.key in {"eps", "dividend_per_share"} and _matches_any(metric.patterns, line)
        ]
        if per_share_metrics:
            return per_share_metrics

    matched = [
        (position, metric)
        for metric in METRIC_DEFINITIONS
        if (position := _first_match_position(metric.patterns, line)) is not None
    ]
    return [metric for _, metric in sorted(matched, key=lambda item: item[0])]


def _split_markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    return [_clean_cell(cell) for cell in stripped.strip("|").split("|")]


def _clean_cell(value: str) -> str:
    cell = normalize_financial_text(value)
    cell = re.sub(r"<br\s*/?>", " ", cell, flags=re.IGNORECASE)
    cell = TABLE_COLUMN_MARKER_RE.sub(" ", cell)
    cell = re.sub(r"\s+", " ", cell)
    return cell.strip()


def _is_table_separator(line: str) -> bool:
    return bool(re.fullmatch(r"[\s|:-]+", line))


def _metric_for_table_cell(cell: str) -> MetricDefinition | None:
    if not cell or re.fullmatch(r"Col\d+", cell):
        return None
    if "潜在株式調整後" in cell:
        return None
    metrics = _metrics_in_line(cell)
    return metrics[0] if metrics else None


def _table_header_metrics(line: str) -> list[MetricDefinition | None]:
    cells = _split_markdown_cells(line)
    if len(cells) < 2:
        return []
    first_metric = _metric_for_table_cell(cells[0])
    if first_metric is not None:
        metrics_by_column = [None, first_metric]
        metrics_by_column.extend(_metric_for_table_cell(cell) for cell in cells[1:])
    else:
        metrics_by_column = [None] * len(cells)
        for index, cell in enumerate(cells[1:], 1):
            metrics_by_column[index] = _metric_for_table_cell(cell)
    if sum(1 for metric in metrics_by_column if metric is not None) < 1:
        return []
    return metrics_by_column


def _table_metric_facts(
    *,
    line: str,
    line_index: int,
    metrics_by_column: list[MetricDefinition | None],
) -> list[dict[str, object]]:
    cells = _split_markdown_cells(line)
    if len(cells) < 2 or _is_table_separator(line):
        return []

    row_kind = _forecast_row_kind(line)
    has_financial_unit = any(re.search(r"百万円|千円|億円|円|銭|%|％", cell) for cell in cells[1:])
    has_data_label = bool(re.search(r"年|期|予想|実績|今回|前回|増減", cells[0]))
    if row_kind is None and not has_financial_unit and re.search(r"第\d四半期末|期末|合計", line):
        return []
    if row_kind is None and not has_financial_unit and not has_data_label:
        return []

    if row_kind:
        mapped_values = []
        for index, cell in enumerate(cells[1:], 1):
            metric = metrics_by_column[index] if index < len(metrics_by_column) else None
            for value in _extract_numeric_values(cell):
                payload = dict(value)
                if metric is not None:
                    payload["metric"] = metric.key
                    payload["metric_label_ja"] = metric.label_ja
                    payload["metric_unit"] = metric.unit
                mapped_values.append(payload)
        if mapped_values:
            return [
                {
                    "type": "forecast_revision_row",
                    "row_kind": row_kind,
                    "values": mapped_values,
                    "source": _source_line_payload(line, line_index),
                    "confidence": 0.72,
                }
            ]

    facts: list[dict[str, object]] = []
    for index, cell in enumerate(cells[1:], 1):
        metric = metrics_by_column[index] if index < len(metrics_by_column) else None
        if metric is None or cell.startswith(("%", "％")):
            continue
        values = _extract_numeric_values(cell)
        values = _drop_label_numbers(cell, values)
        if not values:
            continue
        facts.append(
            {
                "type": "metric_row",
                "metric": metric.key,
                "metric_label_ja": metric.label_ja,
                "unit": metric.unit,
                "values": values,
                "source": _source_line_payload(line, line_index),
                "confidence": 0.72,
            }
        )
    return facts


def _forecast_row_kind(line: str) -> str | None:
    cells = _split_markdown_cells(line)
    label = cells[0] if cells else line
    label = label.strip()
    if re.match(r"^[\(（]\d+[\)）]", label):
        return None
    if "表示は" in line and "予想" not in label:
        return None

    for row_kind, pattern in FORECAST_ROW_PATTERNS:
        if re.search(pattern, label[:80], flags=re.IGNORECASE):
            return row_kind
    return None


def _parse_numeric_value(raw_value: str) -> dict[str, object] | None:
    raw = normalize_financial_text(raw_value).strip()
    if not raw or raw in {"-", "△"}:
        return None
    if re.fullmatch(r"\(\d\)", raw):
        return None

    is_percent = raw.endswith("%")
    raw_without_unit = raw.rstrip("%")
    negative = raw_without_unit.startswith(("△", "-")) or (
        raw_without_unit.startswith("(") and raw_without_unit.endswith(")")
    )
    numeric_text = raw_without_unit.strip("△-()").replace(",", "")
    if not numeric_text:
        return None
    try:
        value = float(numeric_text) if "." in numeric_text else int(numeric_text)
    except ValueError:
        return None
    if negative:
        value = -value
    return {
        "raw": raw,
        "value": value,
        "unit": "percent" if is_percent else None,
    }


def _extract_numeric_values(line: str) -> list[dict[str, object]]:
    numeric_source = TABLE_COLUMN_MARKER_RE.sub(" ", line)
    numeric_source = re.sub(r"第\s*\d+\s*四半期", " ", numeric_source)
    numeric_source = re.sub(r"\d{4}\s*年\s*\d+\s*月期?", " ", numeric_source)
    numeric_source = re.sub(r"\d+\s*月\s*\d+\s*日", " ", numeric_source)
    numeric_source = re.sub(r"^\d+\.\s*(?=[^\d\s])", " ", numeric_source)
    values: list[dict[str, object]] = []
    for match in NUMBER_RE.finditer(numeric_source):
        parsed = _parse_numeric_value(match.group(0))
        if parsed is not None:
            values.append(parsed)
    return values


def _drop_label_numbers(line: str, values: list[dict[str, object]]) -> list[dict[str, object]]:
    if values and re.search(r"1\s*株", line) and values[0].get("value") == 1:
        return values[1:]
    return values


def _source_line_payload(line: str, line_index: int) -> dict[str, object]:
    return {
        "line_index": line_index,
        "text": line[:1000],
    }


def extract_financial_facts(
    *,
    title: str,
    content_text: str,
    disclosure_id: str | None = None,
    code: str | None = None,
    disclosure_date: date | None = None,
    analyzer_version: str = FINANCIAL_FACTS_ANALYZER_VERSION,
) -> dict[str, object]:
    """Extract a conservative set of structured financial facts from parsed text."""
    lines = _line_iter(content_text)
    facts: list[dict[str, object]] = []
    current_header_metrics: list[MetricDefinition] = []
    current_table_metrics: list[MetricDefinition | None] = []

    for line_index, line in enumerate(lines, 1):
        table_metrics = _table_header_metrics(line)
        if table_metrics:
            current_table_metrics = table_metrics
            current_header_metrics = [metric for metric in table_metrics if metric is not None]
            continue
        if current_table_metrics:
            table_facts = _table_metric_facts(
                line=line,
                line_index=line_index,
                metrics_by_column=current_table_metrics,
            )
            if table_facts:
                facts.extend(table_facts)
                continue
            if not _is_table_separator(line):
                current_table_metrics = []

        values = _extract_numeric_values(line)
        metrics = _metrics_in_line(line)
        row_kind = _forecast_row_kind(line)

        if len(metrics) >= 2 and not row_kind:
            current_header_metrics = metrics
            if len(values) <= len(metrics):
                continue

        values = _drop_label_numbers(line, values)

        if values and row_kind:
            mapped_values = []
            for value_index, value in enumerate(values):
                payload = dict(value)
                if value_index < len(current_header_metrics):
                    metric = current_header_metrics[value_index]
                    payload["metric"] = metric.key
                    payload["metric_label_ja"] = metric.label_ja
                    payload["metric_unit"] = metric.unit
                mapped_values.append(payload)
            facts.append(
                {
                    "type": "forecast_revision_row",
                    "row_kind": row_kind,
                    "values": mapped_values,
                    "source": _source_line_payload(line, line_index),
                    "confidence": 0.7 if mapped_values else 0.5,
                }
            )

        if values and metrics and len(metrics) == 1:
            for metric in metrics:
                facts.append(
                    {
                        "type": "metric_row",
                        "metric": metric.key,
                        "metric_label_ja": metric.label_ja,
                        "unit": metric.unit,
                        "values": values,
                        "source": _source_line_payload(line, line_index),
                        "confidence": 0.78,
                    }
                )

    metric_keys = {str(fact.get("metric")) for fact in facts if fact.get("metric")}
    for fact in facts:
        values = fact.get("values")
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and value.get("metric"):
                metric_keys.add(str(value["metric"]))
    forecast_row_count = sum(1 for fact in facts if fact.get("type") == "forecast_revision_row")
    return {
        "schema_version": 1,
        "analysis_type": FINANCIAL_FACTS_ANALYSIS_TYPE,
        "analyzer_name": FINANCIAL_FACTS_ANALYZER_NAME,
        "analyzer_version": analyzer_version,
        "document": {
            "disclosure_id": disclosure_id,
            "code": code,
            "disclosure_date": disclosure_date.isoformat() if disclosure_date else None,
            "title": title,
        },
        "facts": facts,
        "summary": {
            "fact_count": len(facts),
            "metric_keys": sorted(metric_keys),
            "forecast_revision_rows": forecast_row_count,
            "has_forecast_revision": forecast_row_count > 0,
        },
    }


def summarize_financial_facts(result_json: dict[str, object]) -> str:
    summary = result_json.get("summary") if isinstance(result_json, dict) else None
    if not isinstance(summary, dict):
        return "financial_facts fact_count=0"
    metric_keys = summary.get("metric_keys")
    metrics = ",".join(str(metric) for metric in metric_keys) if isinstance(metric_keys, list) else ""
    return (
        f"financial_facts fact_count={summary.get('fact_count', 0)} "
        f"metrics={metrics or '-'} forecast_revision_rows={summary.get('forecast_revision_rows', 0)}"
    )


def _analysis_filter(
    *,
    file_id_column,
    parse_job_id_column,
    analyzer_version: str,
):
    return (
        DocumentAnalysisResultRecord.file_id == file_id_column,
        DocumentAnalysisResultRecord.parse_job_id == parse_job_id_column,
        DocumentAnalysisResultRecord.analysis_type == FINANCIAL_FACTS_ANALYSIS_TYPE,
        DocumentAnalysisResultRecord.analyzer_name == FINANCIAL_FACTS_ANALYZER_NAME,
        DocumentAnalysisResultRecord.analyzer_version == analyzer_version,
    )


def _candidate_statement(
    *,
    parser_name: str,
    parser_version: str | None,
    analyzer_version: str,
    retry_failed: bool,
    force: bool,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
):
    completed_analysis = (
        select(DocumentAnalysisResultRecord.id)
        .where(*_analysis_filter(
            file_id_column=DisclosureFileRecord.id,
            parse_job_id_column=DocumentParseJobRecord.id,
            analyzer_version=analyzer_version,
        ))
        .where(DocumentAnalysisResultRecord.status == "completed")
        .exists()
    )
    failed_analysis = (
        select(DocumentAnalysisResultRecord.id)
        .where(*_analysis_filter(
            file_id_column=DisclosureFileRecord.id,
            parse_job_id_column=DocumentParseJobRecord.id,
            analyzer_version=analyzer_version,
        ))
        .where(DocumentAnalysisResultRecord.status == "failed")
        .exists()
    )
    if force:
        analysis_needed = true()
    elif retry_failed:
        analysis_needed = ~completed_analysis
    else:
        analysis_needed = and_(~completed_analysis, ~failed_analysis)

    stmt = (
        select(DisclosureRecord, DisclosureFileRecord, DocumentParseJobRecord, DocumentParseTextRecord)
        .join(DisclosureFileRecord, DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .join(DocumentParseJobRecord, DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .join(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
        .where(DisclosureFileRecord.file_type == "pdf")
        .where(DocumentParseJobRecord.parse_status == "completed")
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(analysis_needed)
    )
    if parser_version is not None:
        stmt = stmt.where(DocumentParseJobRecord.parser_version == parser_version)
    if date_from is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    if code is not None:
        stmt = stmt.where(DisclosureRecord.code == code.strip().upper())
    return stmt.order_by(
        DisclosureRecord.disclosure_date.desc(),
        DisclosureRecord.time.desc(),
        DocumentParseJobRecord.id.desc(),
    )


async def select_financial_fact_candidates(
    session: AsyncSession,
    *,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
    analyzer_version: str = FINANCIAL_FACTS_ANALYZER_VERSION,
    retry_failed: bool = False,
    force: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
    limit: int = 100,
) -> list[FinancialFactCandidate]:
    version = parser_version if parser_version is not None else get_parser_version()
    rows = (
        await session.execute(
            _candidate_statement(
                parser_name=parser_name,
                parser_version=version,
                analyzer_version=analyzer_version,
                retry_failed=retry_failed,
                force=force,
                date_from=date_from,
                date_to=date_to,
                code=code,
            ).limit(limit)
        )
    ).all()
    return [
        FinancialFactCandidate(
            disclosure=disclosure,
            file_record=file_record,
            parse_job=parse_job,
            parse_text=parse_text,
        )
        for disclosure, file_record, parse_job, parse_text in rows
    ]


async def count_financial_fact_candidates(
    session: AsyncSession,
    *,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
    analyzer_version: str = FINANCIAL_FACTS_ANALYZER_VERSION,
    retry_failed: bool = False,
    force: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
) -> int:
    version = parser_version if parser_version is not None else get_parser_version()
    stmt = _candidate_statement(
        parser_name=parser_name,
        parser_version=version,
        analyzer_version=analyzer_version,
        retry_failed=retry_failed,
        force=force,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    return int(await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)


async def _get_or_create_analysis_result(
    session: AsyncSession,
    *,
    candidate: FinancialFactCandidate,
    analyzer_version: str,
) -> DocumentAnalysisResultRecord:
    stmt = (
        select(DocumentAnalysisResultRecord)
        .where(
            DocumentAnalysisResultRecord.file_id == candidate.file_record.id,
            DocumentAnalysisResultRecord.parse_job_id == candidate.parse_job.id,
            DocumentAnalysisResultRecord.analysis_type == FINANCIAL_FACTS_ANALYSIS_TYPE,
            DocumentAnalysisResultRecord.analyzer_name == FINANCIAL_FACTS_ANALYZER_NAME,
            DocumentAnalysisResultRecord.analyzer_version == analyzer_version,
        )
        .order_by(DocumentAnalysisResultRecord.id.desc())
        .limit(1)
    )
    record = await session.scalar(stmt)
    if record is not None:
        return record

    record = DocumentAnalysisResultRecord(
        file_id=candidate.file_record.id,
        parse_job_id=candidate.parse_job.id,
        analysis_type=FINANCIAL_FACTS_ANALYSIS_TYPE,
        analyzer_name=FINANCIAL_FACTS_ANALYZER_NAME,
        analyzer_version=analyzer_version,
        status="pending",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def _start_analysis(session: AsyncSession, record: DocumentAnalysisResultRecord) -> None:
    record.status = "running"
    record.last_analysis_error = None
    await session.commit()


async def _complete_analysis(
    session: AsyncSession,
    record: DocumentAnalysisResultRecord,
    *,
    result_json: dict[str, object],
) -> None:
    record.status = "completed"
    record.result_json = result_json
    record.result_text = summarize_financial_facts(result_json)
    record.analyzed_at = datetime.now(timezone.utc)
    record.last_analysis_error = None
    await session.commit()


async def _fail_analysis(session: AsyncSession, record: DocumentAnalysisResultRecord, error: str) -> None:
    record.status = "failed"
    record.last_analysis_error = error[:4000]
    await session.commit()


async def analyze_financial_facts(
    session: AsyncSession,
    *,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
    analyzer_version: str = FINANCIAL_FACTS_ANALYZER_VERSION,
    retry_failed: bool = False,
    force: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
    limit: int = 100,
) -> FinancialFactAnalysisSummary:
    started = time.monotonic()
    total_pending = await count_financial_fact_candidates(
        session,
        parser_name=parser_name,
        parser_version=parser_version,
        analyzer_version=analyzer_version,
        retry_failed=retry_failed,
        force=force,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    candidates = await select_financial_fact_candidates(
        session,
        parser_name=parser_name,
        parser_version=parser_version,
        analyzer_version=analyzer_version,
        retry_failed=retry_failed,
        force=force,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
    )

    analyzed = 0
    failed = 0
    fact_counts: dict[str, int] = {}
    for candidate in candidates:
        record = await _get_or_create_analysis_result(
            session,
            candidate=candidate,
            analyzer_version=analyzer_version,
        )
        try:
            await _start_analysis(session, record)
            result_json = extract_financial_facts(
                title=candidate.disclosure.title,
                content_text=candidate.parse_text.content_text,
                disclosure_id=candidate.disclosure.id,
                code=candidate.disclosure.code,
                disclosure_date=candidate.disclosure.disclosure_date,
                analyzer_version=analyzer_version,
            )
            await _complete_analysis(session, record, result_json=result_json)
            analyzed += 1
            summary = result_json.get("summary")
            if isinstance(summary, dict):
                for metric_key in summary.get("metric_keys", []):
                    fact_counts[str(metric_key)] = fact_counts.get(str(metric_key), 0) + 1
                if summary.get("has_forecast_revision"):
                    fact_counts["forecast_revision"] = fact_counts.get("forecast_revision", 0) + 1
        except Exception as exc:  # pragma: no cover - defensive persistence path
            failed += 1
            await _fail_analysis(session, record, str(exc))

    return FinancialFactAnalysisSummary(
        total_pending=total_pending,
        candidates=len(candidates),
        analyzed=analyzed,
        skipped=max(0, len(candidates) - analyzed - failed),
        failed=failed,
        elapsed_seconds=time.monotonic() - started,
        fact_counts=fact_counts,
    )
