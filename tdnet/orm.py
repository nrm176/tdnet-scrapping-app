"""SQLAlchemy ORM models for persisted TDnet disclosures."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DisclosureRecord(Base):
    __tablename__ = "tdnet_disclosures"
    __table_args__ = (
        UniqueConstraint("pdf_url", name="uq_tdnet_disclosures_pdf_url"),
        Index("ix_tdnet_disclosures_date_time", "disclosure_date", "time"),
        Index("ix_tdnet_disclosures_code_date", "code", "disclosure_date"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    disclosure_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    time: Mapped[str] = mapped_column(String(5), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_url: Mapped[str] = mapped_column(Text, nullable=False)
    xbrl_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    xbrl_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    place: Mapped[str] = mapped_column(String(10), nullable=False)
    history: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    files: Mapped[list["DisclosureFileRecord"]] = relationship(
        back_populates="disclosure",
        cascade="all, delete-orphan",
    )
    parse_reviews: Mapped[list["DocumentParseReviewRecord"]] = relationship(
        back_populates="disclosure",
        cascade="all, delete-orphan",
    )
    tag_assignments: Mapped[list["ReportTagAssignmentRecord"]] = relationship(
        back_populates="disclosure",
        cascade="all, delete-orphan",
    )


class DisclosureFileRecord(Base):
    __tablename__ = "disclosure_files"
    __table_args__ = (
        UniqueConstraint("disclosure_id", "file_type", name="uq_disclosure_files_disclosure_type"),
        Index("ix_disclosure_files_status_type", "download_status", "file_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[str] = mapped_column(
        ForeignKey("tdnet_disclosures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    download_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    download_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_download_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    disclosure: Mapped[DisclosureRecord] = relationship(back_populates="files")
    parse_jobs: Mapped[list["DocumentParseJobRecord"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
    )
    analysis_results: Mapped[list["DocumentAnalysisResultRecord"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
    )
    tag_assignments: Mapped[list["ReportTagAssignmentRecord"]] = relationship(
        back_populates="file",
    )


class DocumentParseJobRecord(Base):
    __tablename__ = "document_parse_jobs"
    __table_args__ = (
        UniqueConstraint("file_id", "parser_name", "parser_version", name="uq_document_parse_jobs_file_parser"),
        Index("ix_document_parse_jobs_status", "parse_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("disclosure_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parser_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    parse_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    file: Mapped[DisclosureFileRecord] = relationship(back_populates="parse_jobs")
    analysis_results: Mapped[list["DocumentAnalysisResultRecord"]] = relationship(
        back_populates="parse_job",
    )
    parse_text: Mapped["DocumentParseTextRecord | None"] = relationship(
        back_populates="parse_job",
        cascade="all, delete-orphan",
    )
    review_decision: Mapped["DocumentParseReviewRecord | None"] = relationship(
        back_populates="parse_job",
        cascade="all, delete-orphan",
    )
    tag_assignments: Mapped[list["ReportTagAssignmentRecord"]] = relationship(
        back_populates="parse_job",
    )


class DocumentParseTextRecord(Base):
    __tablename__ = "document_parse_texts"
    __table_args__ = (
        UniqueConstraint("parse_job_id", name="uq_document_parse_texts_parse_job"),
        Index("ix_document_parse_texts_parse_job", "parse_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parse_job_id: Mapped[int] = mapped_column(
        ForeignKey("document_parse_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    pages_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    parse_job: Mapped[DocumentParseJobRecord] = relationship(back_populates="parse_text")


class DocumentParseReviewRecord(Base):
    __tablename__ = "document_parse_reviews"
    __table_args__ = (
        UniqueConstraint("parse_job_id", name="uq_document_parse_reviews_parse_job"),
        Index("ix_document_parse_reviews_state", "review_state"),
        Index("ix_document_parse_reviews_disclosure_state", "disclosure_id", "review_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[str] = mapped_column(
        ForeignKey("tdnet_disclosures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parse_job_id: Mapped[int] = mapped_column(
        ForeignKey("document_parse_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_state: Mapped[str] = mapped_column(String(32), nullable=False, default="needs_review", index=True)
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    disclosure: Mapped[DisclosureRecord] = relationship(back_populates="parse_reviews")
    parse_job: Mapped[DocumentParseJobRecord] = relationship(back_populates="review_decision")


class DocumentAnalysisResultRecord(Base):
    __tablename__ = "document_analysis_results"
    __table_args__ = (
        Index("ix_document_analysis_results_type_status", "analysis_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("disclosure_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parse_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_parse_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    analysis_type: Mapped[str] = mapped_column(String(128), nullable=False)
    analyzer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    file: Mapped[DisclosureFileRecord] = relationship(back_populates="analysis_results")
    parse_job: Mapped[DocumentParseJobRecord | None] = relationship(back_populates="analysis_results")


class PipelineRunRecord(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_status_started", "status", "started_at"),
        Index("ix_pipeline_runs_started", "started_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    requested_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkpoint_latest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checkpoint_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checkpoint_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checkpoint_disabled_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    options_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    limits_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    strategies_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    skip_flags_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    log_path: Mapped[str] = mapped_column(Text, nullable=False)
    latest_log_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    failed_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    steps: Mapped[list["PipelineRunStepRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class PipelineRunStepRecord(Base):
    __tablename__ = "pipeline_run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_name", name="uq_pipeline_run_steps_run_step"),
        Index("ix_pipeline_run_steps_run_order", "run_id", "step_order"),
        Index("ix_pipeline_run_steps_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    error_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    run: Mapped[PipelineRunRecord] = relationship(back_populates="steps")


class ReportTagRecord(Base):
    __tablename__ = "tdnet_report_tags"
    __table_args__ = (
        Index("ix_tdnet_report_tags_active_priority", "active", "priority"),
    )

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    label_ja: Mapped[str] = mapped_column(Text, nullable=False)
    label_en: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    assignments: Mapped[list["ReportTagAssignmentRecord"]] = relationship(
        back_populates="tag",
        cascade="all, delete-orphan",
    )


class ReportTagAssignmentRecord(Base):
    __tablename__ = "tdnet_report_tag_assignments"
    __table_args__ = (
        UniqueConstraint("disclosure_id", "tag_slug", name="uq_report_tag_assignments_disclosure_tag"),
        Index("ix_report_tag_assignments_tag_slug", "tag_slug"),
        Index("ix_report_tag_assignments_disclosure_primary", "disclosure_id", "is_primary"),
        Index("ix_report_tag_assignments_tagger", "tagger_name", "tagger_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[str] = mapped_column(
        ForeignKey("tdnet_disclosures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag_slug: Mapped[str] = mapped_column(
        ForeignKey("tdnet_report_tags.slug", ondelete="CASCADE"),
        nullable=False,
    )
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("disclosure_files.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parse_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_parse_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    tagger_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tagger_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    disclosure: Mapped[DisclosureRecord] = relationship(back_populates="tag_assignments")
    tag: Mapped[ReportTagRecord] = relationship(back_populates="assignments")
    file: Mapped[DisclosureFileRecord | None] = relationship(back_populates="tag_assignments")
    parse_job: Mapped[DocumentParseJobRecord | None] = relationship(back_populates="tag_assignments")
