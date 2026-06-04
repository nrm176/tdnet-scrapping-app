"""Static visual review reports for parsed TDnet PDFs."""
from __future__ import annotations

import html
import json
import logging
import random
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .orm import DisclosureFileRecord, DisclosureRecord, DocumentParseJobRecord
from .parsers import PARSER_NAME, get_parser_version

logger = logging.getLogger(__name__)

ReviewStrategy = Literal["suspicious", "random", "recent", "forecast-correction"]
_TEXT_WHITESPACE = {"\n", "\r", "\t"}


@dataclass(frozen=True)
class ParsedPage:
    page: int
    markdown: str
    char_count: int


@dataclass(frozen=True)
class ParseReviewCandidate:
    parse_job_id: int
    file_id: int
    disclosure_id: str
    code: str
    company_name: str
    title: str
    disclosure_date: str
    pdf_path: Path
    text_path: Path
    parser_name: str
    parser_version: str
    file_size_bytes: int | None
    pages: list[ParsedPage]
    warnings: tuple[str, ...]
    suspicion_score: int


@dataclass(frozen=True)
class ParseReviewReport:
    output_dir: Path
    index_path: Path
    reviewed_count: int
    strategy: str


def _load_pages(path: Path) -> list[ParsedPage]:
    pages_path = path.with_name(path.name.replace(".md", ".pages.json"))
    if not pages_path.exists():
        return []
    raw = json.loads(pages_path.read_text(encoding="utf-8"))
    pages: list[ParsedPage] = []
    for index, page in enumerate(raw.get("pages", []), 1):
        if not isinstance(page, dict):
            continue
        markdown = str(page.get("markdown") or "")
        page_number = page.get("page")
        pages.append(
            ParsedPage(
                page=page_number if isinstance(page_number, int) else index,
                markdown=markdown,
                char_count=int(page.get("char_count") or len(markdown)),
            )
        )
    return pages


def _is_japanese_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF66 <= codepoint <= 0xFF9F
    )


def _garbled_text_warnings(text: str) -> tuple[tuple[str, int], ...]:
    if not text:
        return ()

    total_chars = len(text)
    japanese_chars = sum(1 for character in text if _is_japanese_character(character))
    control_chars = sum(1 for character in text if ord(character) < 32 and character not in _TEXT_WHITESPACE)
    replacement_chars = text.count("\ufffd")
    latin1_symbol_chars = sum(1 for character in text if 0x80 <= ord(character) <= 0xFF)
    table_breaks = text.count("<br>")

    warnings: list[tuple[str, int]] = []
    if total_chars >= 300 and japanese_chars == 0 and control_chars / total_chars >= 0.02:
        warnings.append(("high control-character ratio with no Japanese text", 45))
    if total_chars >= 300 and japanese_chars == 0 and latin1_symbol_chars / total_chars >= 0.05:
        warnings.append(("high Latin-1 symbol ratio with no Japanese text", 25))
    if total_chars >= 300 and replacement_chars / total_chars >= 0.01:
        warnings.append(("many replacement characters", 30))
    if table_breaks >= 25 and japanese_chars == 0:
        warnings.append(("markdown table dominated by empty line breaks", 20))
    return tuple(warnings)


def score_pages(
    pages: list[ParsedPage],
    *,
    file_size_bytes: int | None = None,
) -> tuple[int, tuple[str, ...]]:
    if not pages:
        return 100, ("missing page JSON",)

    warnings: list[str] = []
    score = 0
    total_chars = sum(page.char_count for page in pages)
    average_chars = total_chars / len(pages)
    low_text_pages = sum(1 for page in pages if page.char_count < 120)
    combined_text = "\n".join(page.markdown for page in pages)

    if total_chars < 300:
        score += 40
        warnings.append("very low total text")
    if average_chars < 250:
        score += 25
        warnings.append("low average text per page")
    if low_text_pages:
        score += min(30, low_text_pages * 5)
        warnings.append(f"{low_text_pages} low-text page(s)")
    if len(pages) >= 10 and total_chars < len(pages) * 250:
        score += 20
        warnings.append("multi-page PDF with sparse text")
    if file_size_bytes and file_size_bytes > 2_000_000 and total_chars < 1000:
        score += 25
        warnings.append("large PDF with sparse extracted text")
    for warning, warning_score in _garbled_text_warnings(combined_text):
        score += warning_score
        warnings.append(warning)

    return score, tuple(warnings or ("looks normal",))


async def select_review_candidates(
    session: AsyncSession,
    *,
    strategy: ReviewStrategy = "suspicious",
    limit: int = 50,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
) -> list[ParseReviewCandidate]:
    version = parser_version or get_parser_version()
    stmt = (
        select(DocumentParseJobRecord, DisclosureFileRecord, DisclosureRecord)
        .join(DisclosureFileRecord, DocumentParseJobRecord.file_id == DisclosureFileRecord.id)
        .join(DisclosureRecord, DisclosureFileRecord.disclosure_id == DisclosureRecord.id)
        .where(DocumentParseJobRecord.parse_status == "completed")
        .where(DocumentParseJobRecord.parser_name == parser_name)
        .where(DocumentParseJobRecord.parser_version == version)
        .where(DocumentParseJobRecord.text_path.is_not(None))
    )

    if strategy == "random":
        stmt = stmt.order_by(func.random()).limit(limit)
    elif strategy == "recent":
        stmt = stmt.order_by(DocumentParseJobRecord.parsed_at.desc(), DocumentParseJobRecord.id.desc()).limit(limit)
    elif strategy == "forecast-correction":
        stmt = (
            stmt.where(
                (DisclosureRecord.title.contains("業績予想"))
                | (DisclosureRecord.title.contains("予想値"))
            )
            .order_by(DocumentParseJobRecord.parsed_at.desc(), DocumentParseJobRecord.id.desc())
            .limit(limit)
        )
    else:
        stmt = stmt.order_by(DocumentParseJobRecord.parsed_at.desc(), DocumentParseJobRecord.id.desc()).limit(limit * 20)

    rows = (await session.execute(stmt)).all()
    candidates: list[ParseReviewCandidate] = []
    for parse_job, file_record, disclosure in rows:
        text_path = Path(parse_job.text_path)
        pages = _load_pages(text_path)
        score, warnings = score_pages(pages, file_size_bytes=file_record.file_size_bytes)
        candidates.append(
            ParseReviewCandidate(
                parse_job_id=parse_job.id,
                file_id=file_record.id,
                disclosure_id=disclosure.id,
                code=disclosure.code,
                company_name=disclosure.name,
                title=disclosure.title,
                disclosure_date=str(disclosure.disclosure_date),
                pdf_path=Path(file_record.storage_path),
                text_path=text_path,
                parser_name=parse_job.parser_name,
                parser_version=parse_job.parser_version,
                file_size_bytes=file_record.file_size_bytes,
                pages=pages,
                warnings=warnings,
                suspicion_score=score,
            )
        )

    if strategy == "suspicious":
        candidates.sort(key=lambda candidate: candidate.suspicion_score, reverse=True)
        return candidates[:limit]
    if strategy == "random":
        random.shuffle(candidates)
    return candidates[:limit]


def render_pdf_page(pdf_path: Path, page_number: int, destination: Path, *, zoom: float = 1.5) -> None:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - older PyMuPDF import name
        import fitz as pymupdf

    destination.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(pymupdf, "TOOLS"):
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
    document = pymupdf.open(pdf_path)
    try:
        page_index = max(0, page_number - 1)
        if page_index >= document.page_count:
            return
        page = document.load_page(page_index)
        matrix = pymupdf.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        pixmap.save(destination)
    finally:
        document.close()


async def build_parse_review_report(
    session: AsyncSession,
    *,
    output_root: Path = Path("parse-reviews"),
    strategy: ReviewStrategy = "suspicious",
    limit: int = 50,
    pages_per_file: int = 2,
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
    open_report: bool = False,
) -> ParseReviewReport:
    candidates = await select_review_candidates(
        session,
        strategy=strategy,
        limit=limit,
        parser_name=parser_name,
        parser_version=parser_version,
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = output_root / stamp
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    image_map: dict[tuple[int, int], Path] = {}
    for candidate in candidates:
        selected_pages = candidate.pages[: max(1, pages_per_file)] or [ParsedPage(page=1, markdown="", char_count=0)]
        for page in selected_pages:
            image_path = assets_dir / f"file-{candidate.file_id}-page-{page.page:03d}.png"
            try:
                render_pdf_page(candidate.pdf_path, page.page, image_path)
                image_map[(candidate.file_id, page.page)] = image_path.relative_to(output_dir)
            except Exception as exc:
                logger.warning(
                    "Failed to render review image file_id=%s page=%s error=%s",
                    candidate.file_id,
                    page.page,
                    exc,
                )

    index_path = output_dir / "index.html"
    index_path.write_text(
        _render_report_html(candidates, image_map=image_map, pages_per_file=pages_per_file, strategy=strategy),
        encoding="utf-8",
    )
    if open_report:
        webbrowser.open(index_path.resolve().as_uri())
    return ParseReviewReport(
        output_dir=output_dir,
        index_path=index_path,
        reviewed_count=len(candidates),
        strategy=strategy,
    )


def _render_report_html(
    candidates: list[ParseReviewCandidate],
    *,
    image_map: dict[tuple[int, int], Path],
    pages_per_file: int,
    strategy: str,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    items = "\n".join(
        _render_candidate(candidate, image_map=image_map, pages_per_file=pages_per_file)
        for candidate in candidates
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TDnet Parse Review</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #64707d;
      --line: #d8dee6;
      --accent: #0f766e;
      --warn: #9f580a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      padding: 14px 20px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.95);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 20px;
      font-weight: 700;
    }}
    .meta {{
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
    }}
    main {{
      padding: 18px 20px 40px;
    }}
    article {{
      margin-bottom: 20px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }}
    .doc-header {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: grid;
      gap: 8px;
    }}
    .title {{
      font-size: 16px;
      font-weight: 700;
      line-height: 1.4;
    }}
    .badges {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .badge {{
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      background: #eef2f7;
      color: #29313a;
    }}
    .badge.warn {{
      background: #fff7ed;
      color: var(--warn);
      border: 1px solid #fed7aa;
    }}
    .page-row {{
      display: grid;
      grid-template-columns: minmax(360px, 48%) minmax(360px, 52%);
      min-height: 520px;
      border-top: 1px solid var(--line);
    }}
    .pdf-pane, .text-pane {{
      padding: 14px;
      overflow: auto;
    }}
    .pdf-pane {{
      border-right: 1px solid var(--line);
      background: #e9edf2;
    }}
    .pdf-pane img {{
      display: block;
      max-width: 100%;
      margin: 0 auto;
      border: 1px solid #c7d0db;
      background: #fff;
    }}
    .text-pane pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .page-label {{
      margin-bottom: 10px;
      color: var(--accent);
      font-weight: 700;
      font-size: 13px;
    }}
    @media (max-width: 900px) {{
      .page-row {{ grid-template-columns: 1fr; }}
      .pdf-pane {{ border-right: 0; border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>TDnet Parse Review</h1>
    <div class="meta">
      <span>Generated: {html.escape(generated_at)}</span>
      <span>Strategy: {html.escape(strategy)}</span>
      <span>Documents: {len(candidates)}</span>
      <span>Pages per document: {pages_per_file}</span>
    </div>
  </header>
  <main>
    {items or "<p>No completed parse jobs matched this review request.</p>"}
  </main>
</body>
</html>
"""


def _render_candidate(
    candidate: ParseReviewCandidate,
    *,
    image_map: dict[tuple[int, int], Path],
    pages_per_file: int,
) -> str:
    badges = [
        f'<span class="badge">file {candidate.file_id}</span>',
        f'<span class="badge">parse {candidate.parse_job_id}</span>',
        f'<span class="badge">score {candidate.suspicion_score}</span>',
        f'<span class="badge">{len(candidate.pages)} page(s)</span>',
    ]
    badges.extend(
        f'<span class="badge warn">{html.escape(warning)}</span>'
        for warning in candidate.warnings
        if warning != "looks normal"
    )
    selected_pages = candidate.pages[: max(1, pages_per_file)] or [ParsedPage(page=1, markdown="", char_count=0)]
    page_rows = "\n".join(_render_page_row(candidate, page, image_map=image_map) for page in selected_pages)
    return f"""
<article>
  <section class="doc-header">
    <div class="title">{html.escape(candidate.title)}</div>
    <div class="meta">
      <span>{html.escape(candidate.disclosure_date)}</span>
      <span>{html.escape(candidate.code)}</span>
      <span>{html.escape(candidate.company_name)}</span>
      <span>{html.escape(candidate.parser_name)} {html.escape(candidate.parser_version)}</span>
    </div>
    <div class="badges">{''.join(badges)}</div>
  </section>
  {page_rows}
</article>
"""


def _render_page_row(
    candidate: ParseReviewCandidate,
    page: ParsedPage,
    *,
    image_map: dict[tuple[int, int], Path],
) -> str:
    image_path = image_map.get((candidate.file_id, page.page))
    image_html = (
        f'<img src="{html.escape(str(image_path))}" alt="PDF page {page.page}">'
        if image_path
        else "<p>PDF page image was not available.</p>"
    )
    return f"""
<section class="page-row">
  <div class="pdf-pane">
    <div class="page-label">PDF page {page.page}</div>
    {image_html}
  </div>
  <div class="text-pane">
    <div class="page-label">Parsed markdown, {page.char_count} characters</div>
    <pre>{html.escape(page.markdown)}</pre>
  </div>
</section>
"""
