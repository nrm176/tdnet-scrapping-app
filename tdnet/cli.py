"""Command-line interface for scraping and querying TDnet disclosures."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from datetime import date, datetime

from .artifacts import download_pending_files
from .database import SessionLocal, init_db
from .logging_config import configure_logging
from .models import TdnetDisclosure, TdnetScrapingResult
from .ocr import APPLE_VISION_OCR_NAME, get_apple_vision_parser_version, ocr_pending_files
from .parse_texts import backfill_parse_texts
from .parsers import PARSER_NAME, default_parse_workers, get_parser_version, parse_pending_files
from .repository import count_disclosures, query_disclosures, upsert_disclosures
from .review import build_parse_review_report
from .services import scrape_tdnet_by_date
from .stats_logging import format_seconds


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Date must use YYYY-MM-DD format") from exc


def _print_disclosures(disclosures: list[TdnetDisclosure], *, as_json: bool) -> None:
    if as_json:
        print(
            "["
            + ",".join(
                disclosure.model_dump_json(exclude_none=True)
                for disclosure in disclosures
            )
            + "]"
        )
        return

    for i, disclosure in enumerate(disclosures, 1):
        print(f"\n{i}. Time: {disclosure.time}")
        print(f"   Code: {disclosure.code}")
        print(f"   Company: {disclosure.name}")
        print(f"   Title: {disclosure.title}")
        print(f"   Exchange: {disclosure.place}")
        print(f"   PDF URL: {disclosure.pdf_url}")
        if disclosure.xbrl_available:
            print("   XBRL: Available")
            if disclosure.xbrl_url:
                print(f"   XBRL URL: {disclosure.xbrl_url}")
        print("-" * 50)


def _print_scrape_result(result: TdnetScrapingResult, output_format: str, as_json: bool) -> None:
    if output_format in ["urls", "both"]:
        if result.pdf_urls:
            print("\n--- PDF URLs ---")
            print(f"Total unique URLs found: {len(result.pdf_urls)}")
            print("----------------")
            for url in result.pdf_urls:
                print(str(url))
        else:
            logging.info("No PDF URLs were found for the specified date.")

    if output_format in ["structured", "both"]:
        if result.disclosures:
            print("\n--- Structured Data ---")
            print(f"Total disclosures found: {result.total_disclosures}")
            print("-----------------------")
            if as_json:
                print(result.model_dump_json(indent=2, exclude_none=True))
            else:
                _print_disclosures(result.disclosures, as_json=False)
        else:
            logging.info("No structured data was found for the specified date.")


def _resolve_parser_version(parser_name: str, parser_version: str | None) -> str | None:
    if parser_version is not None:
        return parser_version
    if parser_name == APPLE_VISION_OCR_NAME:
        return get_apple_vision_parser_version()
    if parser_name == PARSER_NAME:
        return get_parser_version()
    return None


async def _persist_result(result: TdnetScrapingResult) -> int:
    await init_db()
    async with SessionLocal() as session:
        return await upsert_disclosures(session, result.disclosures)


async def _list_persisted(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        total = await count_disclosures(
            session,
            disclosure_date=args.date,
            date_from=args.date_from,
            date_to=args.date_to,
            code=args.code,
        )
        disclosures = await query_disclosures(
            session,
            disclosure_date=args.date,
            date_from=args.date_from,
            date_to=args.date_to,
            code=args.code,
            limit=args.limit,
            offset=args.offset,
        )

    if args.json:
        print(
            "{"
            f'"total":{total},"limit":{args.limit},"offset":{args.offset},"disclosures":'
            + "["
            + ",".join(
                disclosure.model_dump_json(exclude_none=True)
                for disclosure in disclosures
            )
            + "]}"
        )
    else:
        print(f"Total persisted disclosures: {total}")
        _print_disclosures(disclosures, as_json=False)
    return 0


async def _download_persisted(args: argparse.Namespace) -> int:
    await init_db()
    logging.info(
        "Starting download job limit=%s concurrency=%s retry_failed=%s root=%s",
        args.limit,
        args.concurrency,
        args.retry_failed,
        args.root,
    )
    async with SessionLocal() as session:
        summary = await download_pending_files(
            session,
            root=Path(args.root) if args.root else None,
            limit=args.limit,
            retry_failed=args.retry_failed,
            concurrency=args.concurrency,
        )
    logging.info(
        "Finished download job candidates=%s downloaded=%s skipped=%s failed=%s",
        summary.candidates,
        summary.downloaded,
        summary.skipped,
        summary.failed,
    )
    print(f"Candidate disclosures: {summary.candidates}")
    print(f"Downloaded files: {summary.downloaded}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Downloaded bytes: {summary.total_bytes}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    return 1 if summary.failed else 0


async def _parse_downloaded(args: argparse.Namespace) -> int:
    await init_db()
    logging.info(
        "Starting parse job limit=%s workers=%s retry_failed=%s",
        args.limit,
        args.workers,
        args.retry_failed,
    )
    async with SessionLocal() as session:
        summary = await parse_pending_files(
            session,
            limit=args.limit,
            retry_failed=args.retry_failed,
            workers=args.workers,
        )
    logging.info(
        "Finished parse job candidates=%s parsed=%s skipped=%s failed=%s elapsed_seconds=%.3f estimated_remaining=%s",
        summary.candidates,
        summary.parsed,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
        format_seconds(summary.estimated_remaining_seconds),
    )
    print(f"Total pending files: {summary.total_pending}")
    print(f"Candidate files: {summary.candidates}")
    print(f"Parsed files: {summary.parsed}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Files per second: {summary.files_per_second:.3f}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    print(f"Estimated total time: {format_seconds(summary.estimated_total_seconds)}")
    print(f"Estimated remaining time: {format_seconds(summary.estimated_remaining_seconds)}")
    return 1 if summary.failed else 0


async def _ocr_downloaded(args: argparse.Namespace) -> int:
    await init_db()
    logging.info(
        "Starting OCR job strategy=%s limit=%s file_id=%s workers=%s retry_failed=%s",
        args.strategy,
        args.limit,
        args.file_id,
        args.workers,
        args.retry_failed,
    )
    async with SessionLocal() as session:
        summary = await ocr_pending_files(
            session,
            strategy=args.strategy,
            limit=args.limit,
            file_id=args.file_id,
            retry_failed=args.retry_failed,
            source_parser_version=args.source_parser_version,
            parser_version=args.parser_version,
            workers=args.workers,
        )
    logging.info(
        "Finished OCR job candidates=%s completed=%s skipped=%s failed=%s elapsed_seconds=%.3f "
        "estimated_remaining=%s",
        summary.candidates,
        summary.ocr_completed,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
        format_seconds(summary.estimated_remaining_seconds),
    )
    print(f"Total pending OCR candidates: {summary.total_pending}")
    print(f"Candidate files: {summary.candidates}")
    print(f"OCR completed files: {summary.ocr_completed}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Files per second: {summary.files_per_second:.3f}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    print(f"Estimated total time: {format_seconds(summary.estimated_total_seconds)}")
    print(f"Estimated remaining time: {format_seconds(summary.estimated_remaining_seconds)}")
    return 1 if summary.failed else 0


async def _review_parses(args: argparse.Namespace) -> int:
    await init_db()
    parser_version = _resolve_parser_version(args.parser_name, args.parser_version)
    async with SessionLocal() as session:
        report = await build_parse_review_report(
            session,
            output_root=Path(args.output_root),
            strategy=args.strategy,
            limit=args.limit,
            pages_per_file=args.pages_per_file,
            parser_name=args.parser_name,
            parser_version=parser_version,
            open_report=args.open,
        )
    print(f"Review strategy: {report.strategy}")
    print(f"Reviewed documents: {report.reviewed_count}")
    print(f"Report: {report.index_path.resolve()}")
    return 0


async def _persist_parse_texts(args: argparse.Namespace) -> int:
    await init_db()
    parser_version = None if args.all_versions else _resolve_parser_version(args.parser_name, args.parser_version)
    logging.info(
        "Starting parse text backfill parser_name=%s parser_version=%s limit=%s all_versions=%s",
        args.parser_name,
        parser_version,
        args.limit,
        args.all_versions,
    )
    async with SessionLocal() as session:
        summary = await backfill_parse_texts(
            session,
            parser_name=args.parser_name,
            parser_version=parser_version,
            limit=args.limit,
        )
    logging.info(
        "Finished parse text backfill candidates=%s persisted=%s skipped=%s failed=%s elapsed_seconds=%.3f "
        "estimated_remaining=%s",
        summary.candidates,
        summary.persisted,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
        format_seconds(summary.estimated_remaining_seconds),
    )
    print(f"Total pending parse text rows: {summary.total_pending}")
    print(f"Candidate parse jobs: {summary.candidates}")
    print(f"Persisted rows: {summary.persisted}")
    print(f"Skipped parse jobs: {summary.skipped}")
    print(f"Failed parse jobs: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Files per second: {summary.files_per_second:.3f}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    print(f"Estimated total time: {format_seconds(summary.estimated_total_seconds)}")
    print(f"Estimated remaining time: {format_seconds(summary.estimated_remaining_seconds)}")
    return 1 if summary.failed else 0


def _run_scrape(args: argparse.Namespace) -> int:
    logging.info("Starting scrape date=%s persist=%s", args.date, args.persist)
    result = scrape_tdnet_by_date(args.date)
    _print_scrape_result(result, args.output_format, args.json)

    if args.persist:
        persisted_count = asyncio.run(_persist_result(result))
        print(f"\nPersisted disclosures: {persisted_count}")
        logging.info("Persisted disclosures date=%s count=%s", args.date, persisted_count)

    if not result.pdf_urls and not result.disclosures:
        logging.info("No data found for the specified date.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape, persist, and query TDnet disclosure data."
    )
    subparsers = parser.add_subparsers(dest="command")

    scrape_parser = subparsers.add_parser("scrape", help="Scrape TDnet for one date.")
    scrape_parser.add_argument("--date", required=True, type=_parse_date)
    scrape_parser.add_argument(
        "--output-format",
        choices=["urls", "structured", "both"],
        default="urls",
    )
    scrape_parser.add_argument("--json", action="store_true")
    scrape_parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist scraped disclosures to PostgreSQL.",
    )
    scrape_parser.set_defaults(handler=_run_scrape)

    list_parser = subparsers.add_parser("list", help="List persisted disclosures.")
    list_parser.add_argument("--date", type=_parse_date)
    list_parser.add_argument("--from", dest="date_from", type=_parse_date)
    list_parser.add_argument("--to", dest="date_to", type=_parse_date)
    list_parser.add_argument("--code")
    list_parser.add_argument("--limit", type=int, default=100)
    list_parser.add_argument("--offset", type=int, default=0)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=lambda args: asyncio.run(_list_persisted(args)))

    download_parser = subparsers.add_parser(
        "download",
        help="Download pending PDF/XBRL files for persisted disclosures.",
    )
    download_parser.add_argument("--limit", type=int, default=100)
    download_parser.add_argument("--concurrency", type=int, default=8)
    download_parser.add_argument(
        "--root",
        help="Override download root. Defaults to TDNET_DOWNLOAD_ROOT.",
    )
    download_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry files previously marked failed.",
    )
    download_parser.set_defaults(handler=lambda args: asyncio.run(_download_persisted(args)))

    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse completed PDF downloads into markdown artifacts.",
    )
    parse_parser.add_argument("--limit", type=int, default=100)
    parse_parser.add_argument(
        "--workers",
        type=int,
        default=default_parse_workers(),
        help="Number of parser worker processes. Defaults to detected CPU cores.",
    )
    parse_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry files previously marked failed for this parser version.",
    )
    parse_parser.set_defaults(handler=lambda args: asyncio.run(_parse_downloaded(args)))

    ocr_parser = subparsers.add_parser(
        "ocr",
        help="Run Apple Vision OCR for completed PDF parses with sparse extracted text.",
    )
    ocr_parser.add_argument("--limit", type=int, default=100)
    ocr_parser.add_argument("--file-id", type=int)
    ocr_parser.add_argument(
        "--strategy",
        choices=["low-text", "forecast-correction", "all"],
        default="low-text",
    )
    ocr_parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent OCR jobs. Apple Vision runs outside Python per PDF.",
    )
    ocr_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry files previously marked failed for this OCR parser version.",
    )
    ocr_parser.add_argument(
        "--source-parser-version",
        help="PyMuPDF parser version to use as the low-text source. Defaults to current version.",
    )
    ocr_parser.add_argument(
        "--parser-version",
        help="Override the Apple Vision OCR parser version identity.",
    )
    ocr_parser.set_defaults(handler=lambda args: asyncio.run(_ocr_downloaded(args)))

    parse_text_parser = subparsers.add_parser(
        "persist-parse-text",
        help="Backfill searchable parse text rows from existing markdown artifacts.",
    )
    parse_text_parser.add_argument("--limit", type=int, default=1000)
    parse_text_parser.add_argument(
        "--parser-name",
        default=PARSER_NAME,
        help="Parser identity to backfill, for example pymupdf4llm or apple-vision-ocr.",
    )
    parse_text_parser.add_argument(
        "--parser-version",
        help="Parser version to backfill. Defaults to the current version for known parsers.",
    )
    parse_text_parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Backfill all versions for the selected parser name.",
    )
    parse_text_parser.set_defaults(handler=lambda args: asyncio.run(_persist_parse_texts(args)))

    review_parser = subparsers.add_parser(
        "review-parse",
        help="Create a visual HTML report for completed PDF parses.",
    )
    review_parser.add_argument("--limit", type=int, default=50)
    review_parser.add_argument(
        "--strategy",
        choices=["suspicious", "random", "recent", "forecast-correction"],
        default="suspicious",
    )
    review_parser.add_argument("--pages-per-file", type=int, default=2)
    review_parser.add_argument("--output-root", default="parse-reviews")
    review_parser.add_argument(
        "--parser-name",
        default="pymupdf4llm",
        help="Parser identity to review, for example pymupdf4llm or apple-vision-ocr.",
    )
    review_parser.add_argument(
        "--parser-version",
        help="Parser version to review. Defaults to the current version for known parsers.",
    )
    review_parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated report in the default browser.",
    )
    review_parser.set_defaults(handler=lambda args: asyncio.run(_review_parses(args)))

    # Backward-compatible legacy flags: tdnet --date ... --output-format ...
    parser.add_argument("--date", type=_parse_date)
    parser.add_argument(
        "--output-format",
        choices=["urls", "structured", "both"],
        default="urls",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--persist", action="store_true")

    return parser


def run(argv: list[str] | None = None) -> int:
    log_path = configure_logging()
    logging.info("Logging to %s", log_path)
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        if args.date is None:
            parser.error("Either use a subcommand or provide --date.")
        args.handler = _run_scrape

    try:
        return args.handler(args)
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}")
        return 1


def main() -> None:
    sys.exit(run())
