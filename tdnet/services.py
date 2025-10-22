"""High-level scraping services (aligned with main.py behavior)."""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .constants import BASE_URL, HEADERS
from .models import TdnetScrapingResult
from .parsing import extract_structured_data_from_page, extract_pdf_urls_from_page, has_next_page


def _build_page_url(target_date: date, page: int) -> str:
    date_str = target_date.strftime("%Y%m%d")
    if page == 1:
        return f"{BASE_URL}/inbs/I_list_001_{date_str}.html"
    page_num_str = f"{page:03d}"
    return f"{BASE_URL}/inbs/I_list_{page_num_str}_{date_str}.html"


def scrape_tdnet_by_date(target_date: date) -> TdnetScrapingResult:
    """Scrape all disclosure PDF URLs and structured data for a specific date using direct URL access."""
    all_found_urls = []
    all_structured_data = []
    current_page = 1

    with requests.Session() as s:
        s.headers.update(HEADERS)

        while True:
            page_url = _build_page_url(target_date, current_page)
            logging.info(f"Requesting data for {target_date.strftime('%Y-%m-%d')}, page {current_page}...")
            logging.info(f"URL: {page_url}")

            try:
                response = s.get(page_url)
                if response.status_code == 404:
                    logging.info(f"Page {current_page} not found (404). Reached end of results.")
                    break
                response.raise_for_status()
                response.encoding = 'utf-8'
            except requests.exceptions.RequestException as e:
                logging.error(f"Failed to retrieve page {current_page}. Error: {e}")
                break

            soup = BeautifulSoup(response.text, 'lxml')

            main_table = soup.find('table', id='main-list-table')
            if (
                soup.find(string='検索条件に該当するデータが見つかりません。')
                or not main_table
                or not main_table.find_all('tr')
            ):
                if current_page == 1:
                    logging.warning(f"No data found for the date: {target_date.strftime('%Y-%m-%d')}")
                else:
                    logging.info("Reached the end of the results.")
                break

            page_urls = extract_pdf_urls_from_page(soup)
            page_structured_data = extract_structured_data_from_page(soup, target_date)

            all_found_urls.extend(page_urls)
            all_structured_data.extend(page_structured_data)

            if not has_next_page(soup):
                logging.info("Last page reached. Concluding scrape for this date.")
                break

            current_page += 1

    unique_urls = sorted(list(set(all_found_urls)))

    return TdnetScrapingResult(
        scraping_date=target_date,
        total_disclosures=len(all_structured_data),
        disclosures=all_structured_data,
        pdf_urls=unique_urls,
    )
