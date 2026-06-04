"""Pydantic schemas for the TDnet review web app API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    database: str


class ParserOption(BaseModel):
    parser_name: str
    parser_version: str
    parse_jobs: int = Field(ge=0)
    parse_texts: int = Field(ge=0)


class ParserOptionsResponse(BaseModel):
    parsers: list[ParserOption]


class ParserQualityParser(BaseModel):
    parser_name: str
    parser_version: str
    total_jobs: int = Field(ge=0)
    completed_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)
    parse_texts: int = Field(ge=0)
    low_text_jobs: int = Field(ge=0)
    average_char_count: float | None


class ParserFallbackCandidate(BaseModel):
    name: str
    parser_name: str
    parser_version: str
    candidate_count: int = Field(ge=0)
    description: str


class ParserRecentError(BaseModel):
    parse_job_id: int
    file_id: int
    parser_name: str
    parser_version: str
    error: str
    updated_at: datetime


class ParserQualityResponse(BaseModel):
    total_jobs: int = Field(ge=0)
    completed_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    parse_texts: int = Field(ge=0)
    low_text_jobs: int = Field(ge=0)
    fallback_candidates: list[ParserFallbackCandidate]
    parsers: list[ParserQualityParser]
    recent_errors: list[ParserRecentError]


class ReportCalendarDay(BaseModel):
    disclosure_date: date
    record_count: int = Field(ge=0)
    report_count: int = Field(ge=0)


class ReportCalendarResponse(BaseModel):
    month: str
    days: list[ReportCalendarDay]


class ReportTagResponse(BaseModel):
    slug: str
    label_ja: str
    label_en: str
    description: str
    priority: int
    active: bool
    assignment_count: int = Field(ge=0)
    primary_count: int = Field(ge=0)


class ReportTagsResponse(BaseModel):
    tags: list[ReportTagResponse]


class ReportTagAssignmentResponse(BaseModel):
    slug: str
    label_ja: str
    label_en: str
    is_primary: bool
    confidence: float = Field(ge=0, le=1)
    source: str


class ParseSearchResult(BaseModel):
    parse_job_id: int
    file_id: int
    disclosure_id: str
    disclosure_date: date
    time: str
    code: str
    company_name: str
    title: str
    parser_name: str
    parser_version: str
    page_count: int
    char_count: int
    parsed_at: datetime | None
    snippet: str
    tags: list[ReportTagAssignmentResponse] = Field(default_factory=list)


class ParseSearchResponse(BaseModel):
    query: str | None
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    results: list[ParseSearchResult]


class ParsedPageResponse(BaseModel):
    page: int
    markdown: str
    char_count: int


class ParseJobDetailResponse(ParseSearchResult):
    source_url: str
    pdf_path: str
    text_path: str | None
    content_text: str
    pages: list[ParsedPageResponse]
