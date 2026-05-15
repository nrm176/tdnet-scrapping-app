"""FastAPI application for persisted TDnet disclosures."""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_session, init_db
from .models import TdnetDisclosure
from .repository import (
    count_disclosure_files,
    count_disclosures,
    get_disclosure,
    query_disclosure_files,
    query_disclosures,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


app = FastAPI(title=settings.api_title, version=settings.api_version, lifespan=lifespan)


class HealthResponse(BaseModel):
    status: str
    database: str


class DisclosureListResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    disclosures: list[TdnetDisclosure]


class DisclosureFileResponse(BaseModel):
    id: int
    disclosure_id: str
    file_type: str
    source_url: str
    source_file_id: str
    storage_bucket: str
    storage_path: str
    content_type: str | None
    file_size_bytes: int | None
    sha256: str | None
    download_status: str
    download_attempts: int
    downloaded_at: datetime | None
    last_download_error: str | None


class DisclosureFileListResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    files: list[DisclosureFileResponse]


@app.get("/health", response_model=HealthResponse)
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> HealthResponse:
    await session.execute(text("select 1"))
    return HealthResponse(status="ok", database="ok")


@app.get("/disclosures", response_model=DisclosureListResponse)
async def list_disclosures(
    session: Annotated[AsyncSession, Depends(get_session)],
    disclosure_date: Annotated[date | None, Query(alias="date")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    code: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureListResponse:
    total = await count_disclosures(
        session,
        disclosure_date=disclosure_date,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    disclosures = await query_disclosures(
        session,
        disclosure_date=disclosure_date,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        offset=offset,
    )
    return DisclosureListResponse(
        total=total,
        limit=limit,
        offset=offset,
        disclosures=disclosures,
    )


@app.get("/disclosures/{disclosure_id}", response_model=TdnetDisclosure)
async def read_disclosure(
    disclosure_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TdnetDisclosure:
    disclosure = await get_disclosure(session, disclosure_id)
    if disclosure is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    return disclosure


@app.get("/disclosure-files", response_model=DisclosureFileListResponse)
async def list_disclosure_files(
    session: Annotated[AsyncSession, Depends(get_session)],
    disclosure_id: str | None = None,
    download_status: str | None = None,
    file_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureFileListResponse:
    total = await count_disclosure_files(
        session,
        disclosure_id=disclosure_id,
        download_status=download_status,
        file_type=file_type,
    )
    records = await query_disclosure_files(
        session,
        disclosure_id=disclosure_id,
        download_status=download_status,
        file_type=file_type,
        limit=limit,
        offset=offset,
    )
    return DisclosureFileListResponse(
        total=total,
        limit=limit,
        offset=offset,
        files=[DisclosureFileResponse.model_validate(record, from_attributes=True) for record in records],
    )


@app.get("/companies/{code}/disclosures", response_model=DisclosureListResponse)
async def list_company_disclosures(
    code: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureListResponse:
    total = await count_disclosures(
        session,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    disclosures = await query_disclosures(
        session,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        offset=offset,
    )
    return DisclosureListResponse(
        total=total,
        limit=limit,
        offset=offset,
        disclosures=disclosures,
    )
