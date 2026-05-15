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
from .parsers import default_parse_workers, parse_pending_files
from .repository import count_disclosures, query_disclosures, upsert_disclosures
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
