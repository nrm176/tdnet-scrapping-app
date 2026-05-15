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
from .models import TdnetDisclosure, TdnetScrapingResult
from .parsers import parse_pending_files
from .repository import count_disclosures, query_disclosures, upsert_disclosures
from .services import scrape_tdnet_by_date


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
    async with SessionLocal() as session:
        summary = await download_pending_files(
            session,
            root=Path(args.root) if args.root else None,
            limit=args.limit,
            retry_failed=args.retry_failed,
        )
    print(f"Candidate disclosures: {summary.candidates}")
    print(f"Downloaded files: {summary.downloaded}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    return 1 if summary.failed else 0


async def _parse_downloaded(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        summary = await parse_pending_files(
            session,
            limit=args.limit,
            retry_failed=args.retry_failed,
        )
    print(f"Candidate files: {summary.candidates}")
    print(f"Parsed files: {summary.parsed}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    return 1 if summary.failed else 0


def _run_scrape(args: argparse.Namespace) -> int:
    result = scrape_tdnet_by_date(args.date)
    _print_scrape_result(result, args.output_format, args.json)

    if args.persist:
        persisted_count = asyncio.run(_persist_result(result))
        print(f"\nPersisted disclosures: {persisted_count}")

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
