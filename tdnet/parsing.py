"""HTML parsing utilities for TDnet pages (aligned with main.py)."""
from __future__ import annotations

import logging
from datetime import date
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .constants import BASE_URL
from .models import TdnetDisclosure


def extract_structured_data_from_page(soup: BeautifulSoup, disclosure_date: date) -> List[TdnetDisclosure]:
    """Parse BeautifulSoup of a page into structured TdnetDisclosure objects (first PDF per row)."""
    structured_data: List[TdnetDisclosure] = []

    main_table = soup.find('table', id='main-list-table')
    if not main_table:
        logging.warning("Could not find main-list-table")
        return structured_data

    rows = main_table.find_all('tr')
    for row in rows:
        cells = row.find_all('td')
        if len(cells) < 6:
            continue

        row_data = {}

        for cell in cells:
            cell_classes = cell.get('class', [])
            for class_name in cell_classes:
                if class_name.startswith('kj'):
                    field_name = class_name[2:].lower()

                    if class_name == 'kjTitle':
                        link_tags = cell.find_all('a')
                        row_data['xbrl_available'] = False

                        for link_tag in link_tags:
                            href = link_tag.get('href', '')
                            if href.endswith('.pdf') and 'pdf_url' not in row_data:
                                row_data['title'] = link_tag.get_text(strip=True)
                                row_data['pdf_url'] = urljoin(BASE_URL, href)
                            elif href.endswith('.zip'):
                                row_data['xbrl_url'] = urljoin(BASE_URL, href)
                                row_data['xbrl_available'] = True

                        if 'title' not in row_data:
                            row_data['title'] = cell.get_text(strip=True)
                    elif class_name == 'kjXbrl':
                        xbrl_link = cell.find('a')
                        if xbrl_link and xbrl_link.get('href', '').endswith('.zip'):
                            row_data['xbrl_url'] = urljoin(BASE_URL, xbrl_link['href'])
                            row_data['xbrl_available'] = True
                        else:
                            row_data['xbrl_available'] = False
                    elif class_name == 'kjHistroy':
                        row_data['history'] = cell.get_text(strip=True)
                    elif class_name == 'kjCompany':
                        row_data['name'] = cell.get_text(strip=True)
                    else:
                        row_data[field_name] = cell.get_text(strip=True)

                    break

        if 'time' in row_data and 'code' in row_data and 'pdf_url' in row_data:
            try:
                row_data['disclosure_date'] = disclosure_date
                disclosure = TdnetDisclosure(**row_data)
                structured_data.append(disclosure)
            except Exception as e:
                logging.warning(f"Failed to create TdnetDisclosure object: {e}")

    logging.info(f"Extracted structured data for {len(structured_data)} disclosures on this page.")
    return structured_data


def extract_pdf_urls_from_page(soup: BeautifulSoup) -> List[str]:
    """Find first PDF link per row from the title column (as in main.py)."""
    urls: List[str] = []
    main_table = soup.find('table', id='main-list-table')
    if not main_table:
        logging.warning("Could not find main-list-table")
        return urls

    rows = main_table.find_all('tr')
    for row in rows:
        cells = row.find_all('td')
        if len(cells) >= 4:
            title_cell = cells[3]
            if title_cell and 'kjTitle' in title_cell.get('class', []):
                link_tag = title_cell.find('a')
                if link_tag and 'href' in link_tag.attrs:
                    href = link_tag['href']
                    if href.endswith('.pdf'):
                        urls.append(urljoin(BASE_URL, href))

    logging.info(f"Found {len(urls)} PDF URLs on this page.")
    return urls


def has_next_page(soup: BeautifulSoup) -> bool:
    """Return True if "Next" pager indicates a following page (onclick based)."""
    next_page_div = soup.find('div', class_='pager-R')
    if not next_page_div:
        return False
    onclick_attr = next_page_div.get('onclick', '')
    return 'I_list_' in onclick_attr and '.html' in onclick_attr
