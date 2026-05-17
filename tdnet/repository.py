"""Database repository functions for TDnet disclosures."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TdnetDisclosure
from .orm import DisclosureFileRecord, DisclosureRecord, DocumentParseJobRecord, DocumentParseTextRecord


def _record_values(disclosure: TdnetDisclosure) -> dict[str, object]:
    return {
        "id": disclosure.id,
        "disclosure_date": disclosure.disclosure_date,
        "time": disclosure.time,
        "code": disclosure.code,
        "name": disclosure.name,
        "title": disclosure.title,
        "pdf_url": str(disclosure.pdf_url),
        "xbrl_available": disclosure.xbrl_available,
        "xbrl_url": str(disclosure.xbrl_url) if disclosure.xbrl_url else None,
        "place": disclosure.place,
        "history": disclosure.history,
    }


def disclosure_from_record(record: DisclosureRecord) -> TdnetDisclosure:
    return TdnetDisclosure(
        time=record.time,
        code=record.code,
        name=record.name,
        title=record.title,
        pdf_url=record.pdf_url,
        xbrl_available=record.xbrl_available,
        xbrl_url=record.xbrl_url,
        place=record.place,
        history=record.history,
        disclosure_date=record.disclosure_date,
    )


async def upsert_disclosures(
    session: AsyncSession,
    disclosures: Sequence[TdnetDisclosure],
) -> int:
    """Insert or update disclosures by stable hash ID.

    This intentionally uses ORM merge instead of a PostgreSQL-specific upsert so
    the repository remains easy to test with other async SQLAlchemy backends.
    """
    for disclosure in disclosures:
        await session.merge(DisclosureRecord(**_record_values(disclosure)))
    await session.commit()
    return len(disclosures)


async def get_disclosure(
    session: AsyncSession,
    disclosure_id: str,
) -> TdnetDisclosure | None:
    record = await session.get(DisclosureRecord, disclosure_id)
    if record is None:
        return None
    return disclosure_from_record(record)


def _query_disclosures_statement(
    *,
    disclosure_date: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Select[tuple[DisclosureRecord]]:
    stmt = select(DisclosureRecord)
    if disclosure_date is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date == disclosure_date)
    if date_from is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    if code is not None:
        stmt = stmt.where(DisclosureRecord.code == code.strip().upper())
    return (
        stmt.order_by(
            DisclosureRecord.disclosure_date.desc(),
            DisclosureRecord.time.desc(),
            DisclosureRecord.code.asc(),
        )
        .limit(limit)
        .offset(offset)
    )


async def query_disclosures(
    session: AsyncSession,
    *,
    disclosure_date: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TdnetDisclosure]:
    stmt = _query_disclosures_statement(
        disclosure_date=disclosure_date,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        offset=offset,
    )
    records = (await session.scalars(stmt)).all()
    return [disclosure_from_record(record) for record in records]


async def count_disclosures(
    session: AsyncSession,
    *,
    disclosure_date: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    code: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(DisclosureRecord)
    if disclosure_date is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date == disclosure_date)
    if date_from is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(DisclosureRecord.disclosure_date <= date_to)
    if code is not None:
        stmt = stmt.where(DisclosureRecord.code == code.strip().upper())
    return int(await session.scalar(stmt) or 0)


async def iter_disclosures_for_download(
    session: AsyncSession,
    *,
    limit: int = 100,
    retry_failed: bool = False,
) -> list[DisclosureRecord]:
    pdf_done = (
        select(DisclosureFileRecord.id)
        .where(DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .where(DisclosureFileRecord.file_type == "pdf")
        .where(DisclosureFileRecord.download_status == "completed")
        .exists()
    )
    pdf_failed = (
        select(DisclosureFileRecord.id)
        .where(DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .where(DisclosureFileRecord.file_type == "pdf")
        .where(DisclosureFileRecord.download_status == "failed")
        .exists()
    )
    xbrl_done = (
        select(DisclosureFileRecord.id)
        .where(DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .where(DisclosureFileRecord.file_type == "xbrl")
        .where(DisclosureFileRecord.download_status == "completed")
        .exists()
    )
    xbrl_failed = (
        select(DisclosureFileRecord.id)
        .where(DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .where(DisclosureFileRecord.file_type == "xbrl")
        .where(DisclosureFileRecord.download_status == "failed")
        .exists()
    )
    pdf_needed = ~pdf_done if retry_failed else and_(~pdf_done, ~pdf_failed)
    xbrl_needed = ~xbrl_done if retry_failed else and_(~xbrl_done, ~xbrl_failed)
    stmt = (
        select(DisclosureRecord)
        .where(
            or_(
                pdf_needed,
                and_(DisclosureRecord.xbrl_available.is_(True), DisclosureRecord.xbrl_url.is_not(None), xbrl_needed),
            )
        )
        .order_by(DisclosureRecord.disclosure_date.desc(), DisclosureRecord.time.desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def get_or_create_disclosure_file(
    session: AsyncSession,
    *,
    disclosure: DisclosureRecord,
    file_type: str,
    source_url: str,
    source_file_id: str,
    storage_bucket: str,
    storage_path: str,
) -> DisclosureFileRecord:
    stmt = select(DisclosureFileRecord).where(
        DisclosureFileRecord.disclosure_id == disclosure.id,
        DisclosureFileRecord.file_type == file_type,
    )
    record = await session.scalar(stmt)
    if record is not None:
        record.source_url = source_url
        record.source_file_id = source_file_id
        record.storage_bucket = storage_bucket
        record.storage_path = storage_path
        await session.commit()
        await session.refresh(record)
        return record

    record = DisclosureFileRecord(
        disclosure_id=disclosure.id,
        file_type=file_type,
        source_url=source_url,
        source_file_id=source_file_id,
        storage_bucket=storage_bucket,
        storage_path=storage_path,
        download_status="pending",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def complete_disclosure_file(
    session: AsyncSession,
    record: DisclosureFileRecord,
    *,
    file_size_bytes: int,
    sha256: str,
    content_type: str | None,
) -> None:
    record.download_status = "completed"
    record.download_attempts += 1
    record.file_size_bytes = file_size_bytes
    record.sha256 = sha256
    record.content_type = content_type
    record.downloaded_at = datetime.now(timezone.utc)
    record.last_download_error = None
    await session.commit()


async def complete_disclosure_file_by_id(
    session: AsyncSession,
    file_id: int,
    *,
    file_size_bytes: int,
    sha256: str,
    content_type: str | None,
) -> None:
    record = await session.get(DisclosureFileRecord, file_id)
    if record is None:
        raise ValueError(f"Disclosure file not found: {file_id}")
    await complete_disclosure_file(
        session,
        record,
        file_size_bytes=file_size_bytes,
        sha256=sha256,
        content_type=content_type,
    )


async def fail_disclosure_file(
    session: AsyncSession,
    record: DisclosureFileRecord,
    error: str,
) -> None:
    record.download_status = "failed"
    record.download_attempts += 1
    record.last_download_error = error[:4000]
    await session.commit()


async def fail_disclosure_file_by_id(
    session: AsyncSession,
    file_id: int,
    error: str,
) -> None:
    record = await session.get(DisclosureFileRecord, file_id)
    if record is None:
        raise ValueError(f"Disclosure file not found: {file_id}")
    await fail_disclosure_file(session, record, error)


async def query_disclosure_files(
    session: AsyncSession,
    *,
    disclosure_id: str | None = None,
    download_status: str | None = None,
    file_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DisclosureFileRecord]:
    stmt = select(DisclosureFileRecord)
    if disclosure_id is not None:
        stmt = stmt.where(DisclosureFileRecord.disclosure_id == disclosure_id)
    if download_status is not None:
        stmt = stmt.where(DisclosureFileRecord.download_status == download_status)
    if file_type is not None:
        stmt = stmt.where(DisclosureFileRecord.file_type == file_type)
    stmt = stmt.order_by(DisclosureFileRecord.updated_at.desc()).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def count_disclosure_files(
    session: AsyncSession,
    *,
    disclosure_id: str | None = None,
    download_status: str | None = None,
    file_type: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(DisclosureFileRecord)
    if disclosure_id is not None:
        stmt = stmt.where(DisclosureFileRecord.disclosure_id == disclosure_id)
    if download_status is not None:
        stmt = stmt.where(DisclosureFileRecord.download_status == download_status)
    if file_type is not None:
        stmt = stmt.where(DisclosureFileRecord.file_type == file_type)
    return int(await session.scalar(stmt) or 0)


async def iter_files_for_parse(
    session: AsyncSession,
    *,
    parser_name: str,
    parser_version: str,
    limit: int = 100,
    retry_failed: bool = False,
) -> list[DisclosureFileRecord]:
    completed_parse = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(DocumentParseJobRecord.parser_version == parser_version)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .exists()
    )
    failed_parse = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(DocumentParseJobRecord.parser_version == parser_version)
        .where(DocumentParseJobRecord.parse_status == "failed")
        .exists()
    )
    parse_needed = ~completed_parse if retry_failed else and_(~completed_parse, ~failed_parse)
    stmt = (
        select(DisclosureFileRecord)
        .where(DisclosureFileRecord.file_type == "pdf")
        .where(DisclosureFileRecord.download_status == "completed")
        .where(parse_needed)
        .order_by(DisclosureFileRecord.downloaded_at.asc().nullsfirst(), DisclosureFileRecord.id.asc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def count_files_for_parse(
    session: AsyncSession,
    *,
    parser_name: str,
    parser_version: str,
    retry_failed: bool = False,
) -> int:
    completed_parse = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(DocumentParseJobRecord.parser_version == parser_version)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .exists()
    )
    failed_parse = (
        select(DocumentParseJobRecord.id)
        .where(DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(DocumentParseJobRecord.parser_version == parser_version)
        .where(DocumentParseJobRecord.parse_status == "failed")
        .exists()
    )
    parse_needed = ~completed_parse if retry_failed else and_(~completed_parse, ~failed_parse)
    stmt = (
        select(func.count())
        .select_from(DisclosureFileRecord)
        .where(DisclosureFileRecord.file_type == "pdf")
        .where(DisclosureFileRecord.download_status == "completed")
        .where(parse_needed)
    )
    return int(await session.scalar(stmt) or 0)


async def get_or_create_parse_job(
    session: AsyncSession,
    *,
    file_record: DisclosureFileRecord,
    parser_name: str,
    parser_version: str,
) -> DocumentParseJobRecord:
    stmt = select(DocumentParseJobRecord).where(
        DocumentParseJobRecord.file_id == file_record.id,
        DocumentParseJobRecord.parser_name == parser_name,
        DocumentParseJobRecord.parser_version == parser_version,
    )
    record = await session.scalar(stmt)
    if record is not None:
        return record

    record = DocumentParseJobRecord(
        file_id=file_record.id,
        parser_name=parser_name,
        parser_version=parser_version,
        parse_status="pending",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def start_parse_job(
    session: AsyncSession,
    record: DocumentParseJobRecord,
) -> None:
    record.parse_status = "running"
    record.parse_attempts += 1
    record.last_parse_error = None
    await session.commit()


async def complete_parse_job(
    session: AsyncSession,
    record: DocumentParseJobRecord,
    *,
    text_path: str,
    text_sha256: str,
) -> None:
    record.parse_status = "completed"
    record.text_path = text_path
    record.text_sha256 = text_sha256
    record.parsed_at = datetime.now(timezone.utc)
    record.last_parse_error = None
    await session.commit()


async def fail_parse_job(
    session: AsyncSession,
    record: DocumentParseJobRecord,
    error: str,
) -> None:
    record.parse_status = "failed"
    record.last_parse_error = error[:4000]
    await session.commit()


async def upsert_parse_text(
    session: AsyncSession,
    *,
    parse_job: DocumentParseJobRecord,
    content_text: str,
    pages_json: dict | None,
    page_count: int,
    char_count: int,
    content_sha256: str,
) -> DocumentParseTextRecord:
    stmt = select(DocumentParseTextRecord).where(
        DocumentParseTextRecord.parse_job_id == parse_job.id
    )
    record = await session.scalar(stmt)
    if record is None:
        record = DocumentParseTextRecord(
            parse_job_id=parse_job.id,
            content_text=content_text,
            pages_json=pages_json,
            page_count=page_count,
            char_count=char_count,
            content_sha256=content_sha256,
        )
        session.add(record)
    else:
        record.content_text = content_text
        record.pages_json = pages_json
        record.page_count = page_count
        record.char_count = char_count
        record.content_sha256 = content_sha256
    await session.commit()
    await session.refresh(record)
    return record


async def get_parse_text_for_job(
    session: AsyncSession,
    parse_job_id: int,
) -> DocumentParseTextRecord | None:
    stmt = select(DocumentParseTextRecord).where(
        DocumentParseTextRecord.parse_job_id == parse_job_id
    )
    return await session.scalar(stmt)
