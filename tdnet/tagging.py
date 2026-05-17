"""Deterministic report tagging for TDnet disclosures."""
from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .orm import (
    DisclosureFileRecord,
    DisclosureRecord,
    DocumentParseJobRecord,
    DocumentParseTextRecord,
    ReportTagAssignmentRecord,
    ReportTagRecord,
)

TAGGER_NAME = "tdnet-deterministic-hybrid"
TAGGER_VERSION = "1"
TagSource = Literal["title", "content", "title+content"]
TagMode = Literal["any", "all"]


@dataclass(frozen=True)
class ReportTagDefinition:
    slug: str
    label_ja: str
    label_en: str
    description: str
    priority: int
    active: bool = True


@dataclass(frozen=True)
class TagRule:
    slug: str
    title_patterns: tuple[str, ...] = ()
    content_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportTagAssignmentPayload:
    slug: str
    is_primary: bool
    confidence: float
    source: TagSource
    evidence: dict[str, object]


@dataclass(frozen=True)
class ReportClassification:
    primary_tag: str
    assignments: list[ReportTagAssignmentPayload]


@dataclass(frozen=True)
class ReportTagSummary:
    slug: str
    label_ja: str
    label_en: str
    description: str
    priority: int
    active: bool
    assignment_count: int = 0
    primary_count: int = 0


@dataclass(frozen=True)
class ReportTagAssignmentView:
    slug: str
    label_ja: str
    label_en: str
    is_primary: bool
    confidence: float
    source: str


@dataclass(frozen=True)
class ReportTaggingSummary:
    total_pending: int = 0
    candidates: int = 0
    tagged: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0
    tag_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportTagCandidate:
    disclosure: DisclosureRecord
    file_record: DisclosureFileRecord | None
    parse_job: DocumentParseJobRecord | None
    parse_text: DocumentParseTextRecord | None


TAG_DEFINITIONS: tuple[ReportTagDefinition, ...] = (
    ReportTagDefinition("earnings_release", "決算短信", "Earnings release", "Formal earnings release documents.", 10),
    ReportTagDefinition("earnings_materials", "決算説明・補足資料", "Earnings materials", "Earnings presentations and supplementary materials.", 20),
    ReportTagDefinition("forecast_revision", "業績予想・差異", "Forecast revision or variance", "Forecast revisions and differences between forecasts and results.", 30),
    ReportTagDefinition("etf_fund_disclosure", "ETF・投信・REIT", "ETF, fund, or REIT disclosure", "ETF, ETN, investment trust, fund, and REIT disclosures.", 34),
    ReportTagDefinition("dividend_distribution", "配当・分配", "Dividend or distribution", "Dividends, distribution amounts, and related payout notices.", 35),
    ReportTagDefinition("share_buyback", "自己株式", "Share buyback", "Share repurchases, treasury stock disposal, and cancellation.", 40),
    ReportTagDefinition("management_change", "役員・人事", "Management change", "Director, auditor, representative, and executive changes.", 50),
    ReportTagDefinition("governance_meeting", "株主総会・ガバナンス", "Governance or shareholder meeting", "Shareholder meetings, articles of incorporation, proposals, and governance matters.", 60),
    ReportTagDefinition("m_and_a_reorganization", "M&A・組織再編", "M&A or reorganization", "Mergers, acquisitions, tender offers, subsidiaries, and group reorganizations.", 70),
    ReportTagDefinition("financing_capital_action", "資金調達・資本政策", "Financing or capital action", "Equity/debt financing, stock splits, rights, and capital changes.", 80),
    ReportTagDefinition("strategy_plan", "中計・成長戦略", "Strategy or plan", "Medium-term plans, business plans, growth potential, and capital-cost initiatives.", 90),
    ReportTagDefinition("extraordinary_accounting", "特損・会計処理", "Extraordinary or accounting item", "Extraordinary gains/losses, impairments, provisions, and accounting treatment.", 100),
    ReportTagDefinition("audit_internal_control", "監査・内部統制", "Audit or internal control", "Audit, auditor, internal-control, and investigation matters.", 110),
    ReportTagDefinition("shareholder_ownership_listing", "株主・上場・所有", "Shareholder, ownership, or listing", "Major shareholders, parent companies, listing status, and tradability matters.", 120),
    ReportTagDefinition("shareholder_benefit", "株主優待", "Shareholder benefit", "Shareholder benefit program introductions, changes, and abolitions.", 130),
    ReportTagDefinition("correction_change", "訂正・変更", "Correction or change", "Generic corrections, changes, postponements, and amendments.", 140),
    ReportTagDefinition("other", "その他", "Other", "Disclosure did not match a more specific deterministic rule.", 900),
)

TAG_DEFINITION_BY_SLUG = {definition.slug: definition for definition in TAG_DEFINITIONS}

TAG_RULES: tuple[TagRule, ...] = (
    TagRule("earnings_release", title_patterns=(r"決算短信", r"決算速報")),
    TagRule(
        "earnings_materials",
        title_patterns=(
            r"決算説明資料",
            r"決算補足",
            r"補足説明資料",
            r"補足資料",
            r"決算概要",
            r"決算参考資料",
            r"fact sheet",
            r"インベスターズガイド",
            r"financial results",
        ),
        content_patterns=(r"決算説明資料", r"業績ハイライト", r"financial results"),
    ),
    TagRule(
        "forecast_revision",
        title_patterns=(
            r"業績予想",
            r"予想値",
            r"業績予測",
            r"実績値との差異",
            r"実績との差異",
            r"決算値との差異",
            r"通期.*予想",
        ),
        content_patterns=(r"上方修正", r"下方修正", r"今回修正予想", r"修正予想", r"前回発表予想"),
    ),
    TagRule(
        "etf_fund_disclosure",
        title_patterns=(r"\bETF\b", r"\bETN\b", r"\bREIT\b", r"投信", r"上場投信", r"ファンド", r"基準価額", r"市場価格"),
        content_patterns=(r"\bETF\b", r"\bETN\b", r"\bREIT\b", r"投資信託", r"基準価額", r"市場価格"),
    ),
    TagRule(
        "dividend_distribution",
        title_patterns=(r"配当", r"剰余金の配当", r"増配", r"減配", r"収益分配", r"分配金"),
        content_patterns=(r"1株当たり配当", r"配当予想", r"配当金", r"分配金", r"剰余金の配当"),
    ),
    TagRule("share_buyback", title_patterns=(r"自己株式", r"自社株", r"金庫株"), content_patterns=(r"自己株式", r"取得株式総数")),
    TagRule(
        "management_change",
        title_patterns=(r"人事", r"役員", r"代表取締役", r"取締役", r"監査役", r"執行役員", r"社長交代"),
        content_patterns=(r"代表取締役", r"取締役候補", r"監査役候補", r"執行役員"),
    ),
    TagRule(
        "governance_meeting",
        title_patterns=(r"株主総会", r"招集", r"定款", r"議決権", r"株主提案", r"コーポレート.?ガバナンス", r"ガバナンス報告"),
        content_patterns=(r"定時株主総会", r"議案", r"議決権", r"コーポレート.?ガバナンス"),
    ),
    TagRule(
        "m_and_a_reorganization",
        title_patterns=(
            r"\bM&A\b",
            r"合併",
            r"会社分割",
            r"株式交換",
            r"株式移転",
            r"事業譲渡",
            r"子会社",
            r"持分法",
            r"買収",
            r"譲渡",
            r"取得",
            r"\bTOB\b",
            r"公開買付",
            r"経営統合",
        ),
        content_patterns=(r"公開買付", r"経営統合", r"株式譲渡", r"子会社化", r"連結子会社"),
    ),
    TagRule(
        "financing_capital_action",
        title_patterns=(
            r"第三者割当",
            r"新株",
            r"株式分割",
            r"株式併合",
            r"新株予約権",
            r"募集株式",
            r"\bCB\b",
            r"社債",
            r"借入",
            r"資金調達",
            r"資本金",
            r"資本準備金",
            r"立会外分売",
        ),
        content_patterns=(r"第三者割当", r"新株予約権", r"資金調達", r"借入金", r"社債"),
    ),
    TagRule(
        "strategy_plan",
        title_patterns=(r"中期経営計画", r"事業計画", r"成長可能性", r"資本コスト", r"株価を意識", r"経営方針"),
        content_patterns=(r"中期経営計画", r"成長戦略", r"資本コスト", r"企業価値向上"),
    ),
    TagRule(
        "extraordinary_accounting",
        title_patterns=(r"特別利益", r"特別損失", r"減損", r"貸倒", r"引当", r"固定資産", r"棚卸資産", r"営業外収益", r"営業外費用", r"法人税等調整額", r"有価証券含み損"),
        content_patterns=(r"特別利益", r"特別損失", r"減損損失", r"営業外収益", r"営業外費用"),
    ),
    TagRule(
        "audit_internal_control",
        title_patterns=(r"監査", r"内部統制", r"第三者委員会", r"特別調査委員会", r"調査報告書", r"重要な不備", r"会計監査人", r"公認会計士"),
        content_patterns=(r"内部統制", r"監査法人", r"会計監査人", r"監査報告", r"重要な不備", r"調査報告書"),
    ),
    TagRule(
        "shareholder_ownership_listing",
        title_patterns=(r"主要株主", r"支配株主", r"親会社", r"上場廃止", r"整理銘柄", r"貸借銘柄", r"投資単位", r"所有割合", r"大株主"),
        content_patterns=(r"主要株主", r"支配株主", r"親会社", r"上場廃止", r"所有割合"),
    ),
    TagRule("shareholder_benefit", title_patterns=(r"株主優待",), content_patterns=(r"株主優待", r"優待制度")),
    TagRule("correction_change", title_patterns=(r"訂正", r"変更", r"修正", r"延期", r"改定", r"一部変更"), content_patterns=(r"訂正", r"変更", r"修正")),
)


def normalize_tag_text(value: str | None) -> str:
    """Normalize Japanese/full-width text before deterministic matching."""
    if not value:
        return ""
    return unicodedata.normalize("NFKC", value).lower()


def _matching_patterns(patterns: Sequence[str], text: str) -> list[str]:
    if not text:
        return []
    return [pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE)]


def _source_for_matches(title_matches: Sequence[str], content_matches: Sequence[str]) -> TagSource:
    if title_matches and content_matches:
        return "title+content"
    if content_matches:
        return "content"
    return "title"


def _confidence_for_matches(title_matches: Sequence[str], content_matches: Sequence[str]) -> float:
    if title_matches and content_matches:
        return 0.98
    if title_matches:
        return 0.94
    return 0.72


def classify_report(title: str, content_text: str | None = None) -> ReportClassification:
    """Classify one disclosure into deterministic multi-label tags."""
    normalized_title = normalize_tag_text(title)
    normalized_content = normalize_tag_text(content_text)
    matched: dict[str, ReportTagAssignmentPayload] = {}

    for rule in TAG_RULES:
        title_matches = _matching_patterns(rule.title_patterns, normalized_title)
        content_matches = _matching_patterns(rule.content_patterns, normalized_content)
        if not title_matches and not content_matches:
            continue
        matched[rule.slug] = ReportTagAssignmentPayload(
            slug=rule.slug,
            is_primary=False,
            confidence=_confidence_for_matches(title_matches, content_matches),
            source=_source_for_matches(title_matches, content_matches),
            evidence={
                "title_matches": title_matches,
                "content_matches": content_matches,
            },
        )

    if not matched:
        matched["other"] = ReportTagAssignmentPayload(
            slug="other",
            is_primary=True,
            confidence=0.5,
            source="title",
            evidence={"reason": "no_rule_match"},
        )

    primary_slug = min(
        matched,
        key=lambda slug: (
            TAG_DEFINITION_BY_SLUG[slug].priority,
            -matched[slug].confidence,
            slug,
        ),
    )
    assignments = [
        ReportTagAssignmentPayload(
            slug=payload.slug,
            is_primary=payload.slug == primary_slug,
            confidence=payload.confidence,
            source=payload.source,
            evidence=payload.evidence,
        )
        for payload in sorted(
            matched.values(),
            key=lambda item: (
                item.slug != primary_slug,
                TAG_DEFINITION_BY_SLUG[item.slug].priority,
                item.slug,
            ),
        )
    ]
    return ReportClassification(primary_tag=primary_slug, assignments=assignments)


async def upsert_report_tag_definitions(session: AsyncSession) -> None:
    for definition in TAG_DEFINITIONS:
        record = await session.get(ReportTagRecord, definition.slug)
        if record is None:
            record = ReportTagRecord(
                slug=definition.slug,
                label_ja=definition.label_ja,
                label_en=definition.label_en,
                description=definition.description,
                priority=definition.priority,
                active=definition.active,
            )
            session.add(record)
        else:
            record.label_ja = definition.label_ja
            record.label_en = definition.label_en
            record.description = definition.description
            record.priority = definition.priority
            record.active = definition.active
    await session.commit()


def normalize_tag_slugs(tags: Sequence[str] | None) -> list[str]:
    values: list[str] = []
    for raw_value in tags or []:
        for part in str(raw_value).split(","):
            value = part.strip().lower()
            if value and value not in values:
                values.append(value)
    return values


def tag_assignment_exists(disclosure_id_column, tag_slugs: Sequence[str], tag_mode: TagMode = "any"):
    normalized = normalize_tag_slugs(tag_slugs)
    if not normalized:
        return None
    if tag_mode == "all":
        return [
            select(ReportTagAssignmentRecord.id)
            .where(
                ReportTagAssignmentRecord.disclosure_id == disclosure_id_column,
                ReportTagAssignmentRecord.tag_slug == tag_slug,
            )
            .exists()
            for tag_slug in normalized
        ]
    return [
        select(ReportTagAssignmentRecord.id)
        .where(
            ReportTagAssignmentRecord.disclosure_id == disclosure_id_column,
            ReportTagAssignmentRecord.tag_slug.in_(normalized),
        )
        .exists()
    ]


async def count_report_tag_candidates(
    session: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
    force: bool = False,
) -> int:
    stmt = select(func.count(func.distinct(DisclosureRecord.id))).select_from(DisclosureRecord)
    if date_from is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    if code is not None:
        stmt = stmt.where(DisclosureRecord.code == code.strip().upper())
    if not force:
        current_assignment = (
            select(ReportTagAssignmentRecord.id)
            .where(ReportTagAssignmentRecord.disclosure_id == DisclosureRecord.id)
            .where(ReportTagAssignmentRecord.tagger_name == TAGGER_NAME)
            .where(ReportTagAssignmentRecord.tagger_version == TAGGER_VERSION)
            .exists()
        )
        stmt = stmt.where(~current_assignment)
    return int(await session.scalar(stmt) or 0)


async def iter_report_tag_candidates(
    session: AsyncSession,
    *,
    parser_name: str | None = None,
    parser_version: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
    limit: int = 100,
    force: bool = False,
) -> list[ReportTagCandidate]:
    parse_join_conditions = [
        DocumentParseJobRecord.file_id == DisclosureFileRecord.id,
        DocumentParseJobRecord.parse_status == "completed",
    ]
    if parser_name is not None:
        parse_join_conditions.append(DocumentParseJobRecord.parser_name == parser_name)
    if parser_version is not None:
        parse_join_conditions.append(DocumentParseJobRecord.parser_version == parser_version)

    stmt = (
        select(DisclosureRecord, DisclosureFileRecord, DocumentParseJobRecord, DocumentParseTextRecord)
        .outerjoin(
            DisclosureFileRecord,
            and_(
                DisclosureFileRecord.disclosure_id == DisclosureRecord.id,
                DisclosureFileRecord.file_type == "pdf",
                DisclosureFileRecord.download_status == "completed",
            ),
        )
        .outerjoin(DocumentParseJobRecord, and_(*parse_join_conditions))
        .outerjoin(DocumentParseTextRecord, DocumentParseTextRecord.parse_job_id == DocumentParseJobRecord.id)
    )
    if date_from is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    if code is not None:
        stmt = stmt.where(DisclosureRecord.code == code.strip().upper())
    if not force:
        current_assignment = (
            select(ReportTagAssignmentRecord.id)
            .where(ReportTagAssignmentRecord.disclosure_id == DisclosureRecord.id)
            .where(ReportTagAssignmentRecord.tagger_name == TAGGER_NAME)
            .where(ReportTagAssignmentRecord.tagger_version == TAGGER_VERSION)
            .exists()
        )
        stmt = stmt.where(~current_assignment)

    rows = (
        await session.execute(
            stmt.order_by(
                DisclosureRecord.disclosure_date.desc(),
                DisclosureRecord.time.desc(),
                DisclosureRecord.code.asc(),
                DocumentParseTextRecord.char_count.desc().nullslast(),
            ).limit(limit)
        )
    ).all()

    candidates_by_disclosure: dict[str, ReportTagCandidate] = {}
    for disclosure, file_record, parse_job, parse_text in rows:
        if disclosure.id in candidates_by_disclosure:
            existing = candidates_by_disclosure[disclosure.id]
            if existing.parse_text is not None or parse_text is None:
                continue
        candidates_by_disclosure[disclosure.id] = ReportTagCandidate(
            disclosure=disclosure,
            file_record=file_record,
            parse_job=parse_job,
            parse_text=parse_text,
        )
    return list(candidates_by_disclosure.values())


async def replace_report_tag_assignments(
    session: AsyncSession,
    *,
    disclosure_id: str,
    file_id: int | None,
    parse_job_id: int | None,
    assignments: Sequence[ReportTagAssignmentPayload],
) -> None:
    await session.execute(
        delete(ReportTagAssignmentRecord).where(
            ReportTagAssignmentRecord.disclosure_id == disclosure_id
        )
    )
    for assignment in assignments:
        session.add(
            ReportTagAssignmentRecord(
                disclosure_id=disclosure_id,
                tag_slug=assignment.slug,
                file_id=file_id,
                parse_job_id=parse_job_id,
                is_primary=assignment.is_primary,
                confidence=assignment.confidence,
                source=assignment.source,
                evidence_json=assignment.evidence,
                tagger_name=TAGGER_NAME,
                tagger_version=TAGGER_VERSION,
            )
        )
    await session.commit()


async def tag_reports(
    session: AsyncSession,
    *,
    parser_name: str | None = None,
    parser_version: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
    limit: int = 100,
    force: bool = False,
) -> ReportTaggingSummary:
    started_at = time.perf_counter()
    await upsert_report_tag_definitions(session)
    total_pending = await count_report_tag_candidates(
        session,
        date_from=date_from,
        date_to=date_to,
        code=code,
        force=force,
    )
    candidates = await iter_report_tag_candidates(
        session,
        parser_name=parser_name,
        parser_version=parser_version,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        force=force,
    )
    tag_counts: Counter[str] = Counter()
    tagged = skipped = failed = 0
    for candidate in candidates:
        try:
            classification = classify_report(
                candidate.disclosure.title,
                candidate.parse_text.content_text if candidate.parse_text else None,
            )
            await replace_report_tag_assignments(
                session,
                disclosure_id=candidate.disclosure.id,
                file_id=candidate.file_record.id if candidate.file_record else None,
                parse_job_id=candidate.parse_job.id if candidate.parse_job else None,
                assignments=classification.assignments,
            )
            tagged += 1
            tag_counts.update(assignment.slug for assignment in classification.assignments)
        except Exception:
            await session.rollback()
            failed += 1

    return ReportTaggingSummary(
        total_pending=total_pending,
        candidates=len(candidates),
        tagged=tagged,
        skipped=skipped,
        failed=failed,
        elapsed_seconds=time.perf_counter() - started_at,
        tag_counts=dict(sorted(tag_counts.items())),
    )


async def list_report_tag_summaries(session: AsyncSession) -> list[ReportTagSummary]:
    await upsert_report_tag_definitions(session)
    tag_records = (
        await session.scalars(
            select(ReportTagRecord).order_by(ReportTagRecord.priority.asc(), ReportTagRecord.slug.asc())
        )
    ).all()
    count_rows = (
        await session.execute(
            select(
                ReportTagAssignmentRecord.tag_slug,
                func.count(ReportTagAssignmentRecord.id),
                func.sum(case((ReportTagAssignmentRecord.is_primary.is_(True), 1), else_=0)),
            ).group_by(ReportTagAssignmentRecord.tag_slug)
        )
    ).all()
    counts = {
        tag_slug: (int(assignment_count or 0), int(primary_count or 0))
        for tag_slug, assignment_count, primary_count in count_rows
    }
    return [
        ReportTagSummary(
            slug=record.slug,
            label_ja=record.label_ja,
            label_en=record.label_en,
            description=record.description,
            priority=record.priority,
            active=record.active,
            assignment_count=counts.get(record.slug, (0, 0))[0],
            primary_count=counts.get(record.slug, (0, 0))[1],
        )
        for record in tag_records
    ]


async def list_tag_assignments_for_disclosures(
    session: AsyncSession,
    disclosure_ids: Sequence[str],
) -> dict[str, list[ReportTagAssignmentView]]:
    if not disclosure_ids:
        return {}
    unique_ids = list(dict.fromkeys(disclosure_ids))
    rows = (
        await session.execute(
            select(ReportTagAssignmentRecord, ReportTagRecord)
            .join(ReportTagRecord, ReportTagRecord.slug == ReportTagAssignmentRecord.tag_slug)
            .where(ReportTagAssignmentRecord.disclosure_id.in_(unique_ids))
            .order_by(
                ReportTagAssignmentRecord.disclosure_id.asc(),
                ReportTagAssignmentRecord.is_primary.desc(),
                ReportTagRecord.priority.asc(),
                ReportTagRecord.slug.asc(),
            )
        )
    ).all()
    assignments: dict[str, list[ReportTagAssignmentView]] = {disclosure_id: [] for disclosure_id in unique_ids}
    for assignment, tag in rows:
        assignments.setdefault(assignment.disclosure_id, []).append(
            ReportTagAssignmentView(
                slug=assignment.tag_slug,
                label_ja=tag.label_ja,
                label_en=tag.label_en,
                is_primary=assignment.is_primary,
                confidence=assignment.confidence,
                source=assignment.source,
            )
        )
    return assignments
