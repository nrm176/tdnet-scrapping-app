"""Command-line interface for TDnet scraping (mirrors original main.py CLI)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

from .services import scrape_tdnet_by_date


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape TDnet for disclosure URLs and structured data on a specific date."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="The date to scrape in YYYY-MM-DD format.",
        type=str,
    )
    parser.add_argument(
        "--output-format",
        choices=["urls", "structured", "both"],
        default="urls",
        help="Output format: 'urls' for PDF URLs only, 'structured' for structured data, 'both' for both",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured data in JSON format",
    )

    args = parser.parse_args(argv)

    try:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        logging.info(f"Starting scrape for date: {target_date.strftime('%Y-%m-%d')}")
    except ValueError:
        logging.error("Invalid date format. Please use YYYY-MM-DD.")
        return 1

    try:
        result = scrape_tdnet_by_date(target_date)

        if args.output_format in ["urls", "both"]:
            if result.pdf_urls:
                print("\n--- PDF URLs ---")
                print(f"Total unique URLs found: {len(result.pdf_urls)}")
                print("----------------")
                for url in result.pdf_urls:
                    print(str(url))
            else:
                logging.info("No PDF URLs were found for the specified date.")

        if args.output_format in ["structured", "both"]:
            if result.disclosures:
                print(f"\n--- Structured Data ---")
                print(f"Total disclosures found: {result.total_disclosures}")
                print("-----------------------")

                if args.json:
                    print(result.model_dump_json(indent=2, exclude_none=True))
                else:
                    for i, disclosure in enumerate(result.disclosures, 1):
                        print(f"\n{i}. Time: {disclosure.time}")
                        print(f"   Code: {disclosure.code}")
                        print(f"   Company: {disclosure.name}")
                        print(f"   Title: {disclosure.title}")
                        print(f"   Exchange: {disclosure.place}")
                        print(f"   PDF URL: {disclosure.pdf_url}")
                        if disclosure.xbrl_available:
                            print(f"   XBRL: Available")
                            if disclosure.xbrl_url:
                                print(f"   XBRL URL: {disclosure.xbrl_url}")
                        print("-" * 50)
            else:
                logging.info("No structured data was found for the specified date.")

        if not result.pdf_urls and not result.disclosures:
            logging.info("No data found for the specified date.")

    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}")
        return 1

    return 0


def main() -> None:
    """Console script entrypoint.

    Wraps run() and ensures proper exit codes when invoked as a script.
    """
    sys.exit(run())
