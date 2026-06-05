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


class FinancialFactSummaryResponse(BaseModel):
    fact_count: int = Field(ge=0)
    metric_keys: list[str] = Field(default_factory=list)
    forecast_revision_rows: int = Field(ge=0)
    has_forecast_revision: bool


class FinancialFactSourceResponse(BaseModel):
    line_index: int | None
    text: str


class FinancialFactResponse(BaseModel):
    type: str
    row_kind: str | None = None
    metric: str | None = None
    metric_label_ja: str | None = None
    unit: str | None = None
    values: list[dict[str, object]] = Field(default_factory=list)
    source: FinancialFactSourceResponse | None = None
    confidence: float | None = None


class FinancialFactsAnalysisResponse(BaseModel):
    analysis_id: int
    file_id: int
    parse_job_id: int | None
    status: str
    analyzer_name: str
    analyzer_version: str
    analyzed_at: datetime | None
    last_analysis_error: str | None
    result_text: str | None
    summary: FinancialFactSummaryResponse
    facts: list[FinancialFactResponse] = Field(default_factory=list)


class CompanyTimelineFileResponse(BaseModel):
    file_id: int
    file_type: str
    download_status: str
    file_size_bytes: int | None
    downloaded_at: datetime | None
    source_url: str
    storage_path: str


class CompanyTimelineParserResponse(BaseModel):
    parse_job_id: int
    file_id: int
    parser_name: str
    parser_version: str
    parse_status: str
    parse_attempts: int = Field(ge=0)
    parsed_at: datetime | None
    page_count: int | None
    char_count: int | None
    has_text: bool
    last_parse_error: str | None
    financial_facts: FinancialFactsAnalysisResponse | None = None


class CompanyTimelineDisclosureResponse(BaseModel):
    disclosure_id: str
    disclosure_date: date
    time: str
    code: str
    company_name: str
    title: str
    xbrl_available: bool
    tags: list[ReportTagAssignmentResponse] = Field(default_factory=list)
    files: list[CompanyTimelineFileResponse] = Field(default_factory=list)
    parsers: list[CompanyTimelineParserResponse] = Field(default_factory=list)
    best_parse_job_id: int | None
    financial_facts: FinancialFactsAnalysisResponse | None = None
    snippet: str


class CompanyTimelineResponse(BaseModel):
    code: str
    company_name: str | None
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    results: list[CompanyTimelineDisclosureResponse]


class ParsedPageMatchResponse(BaseModel):
    page: int
    snippet: str


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
    matched_pages: list[ParsedPageMatchResponse] = Field(default_factory=list)
    financial_facts: FinancialFactsAnalysisResponse | None = None


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
