"""Compatibility shim delegating to the new tdnet package modules, preserving test patch points."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

# Keep 'requests' available under main.* for tests that patch 'main.requests'
import requests  # noqa: F401

# Re-export public API to keep backward compatibility
from tdnet.constants import BASE_URL, HEADERS
from tdnet.models import (
    TdnetDisclosure,
    TdnetScrapingResult,
    CompanyCode,
    ExchangeCode,
    DisclosureTime,
)
from tdnet.parsing import (
    extract_structured_data_from_page,
    extract_pdf_urls_from_page,
    has_next_page,
)
from tdnet.services import scrape_tdnet_by_date


# Configure logging similar to original
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)


def main() -> None:
    """CLI entrypoint mirroring original behavior and using refactored services."""
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
    args = parser.parse_args()

    try:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        logging.info(f"Starting scrape for date: {target_date.strftime('%Y-%m-%d')}")
    except ValueError:
        logging.error("Invalid date format. Please use YYYY-MM-DD.")
        sys.exit(1)

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
        sys.exit(1)


if __name__ == "__main__":
    main()