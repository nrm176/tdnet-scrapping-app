"""SQLAlchemy ORM models for persisted TDnet disclosures."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
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
