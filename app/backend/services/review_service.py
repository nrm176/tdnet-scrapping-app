"""Review media helpers for the TDnet web app."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tdnet.orm import DisclosureFileRecord, DocumentParseJobRecord
from tdnet.review import render_pdf_page

PAGE_RENDER_ROOT = Path(".app-cache/page-renders")


async def render_parse_job_page(
    session: AsyncSession,
    *,
    parse_job_id: int,
    page: int,
) -> Path | None:
    stmt = (
        select(DocumentParseJobRecord, DisclosureFileRecord)
        .join(DisclosureFileRecord, DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .where(DocumentParseJobRecord.id == parse_job_id)
        .where(DocumentParseJobRecord.parse_status == "completed")
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None

    parse_job, file_record = row
    pdf_path = Path(file_record.storage_path)
    if not pdf_path.exists():
        return None

    page_number = max(1, page)
    output_path = PAGE_RENDER_ROOT / f"parse-{parse_job.id}-page-{page_number:03d}.png"
    if not output_path.exists():
        render_pdf_page(pdf_path, page_number, output_path, zoom=1.7)
    return output_path
