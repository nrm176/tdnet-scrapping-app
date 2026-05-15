"""tdnet package - modular TDnet scraping components.

Public API re-exports for ergonomic imports.
"""
__version__ = "0.1.0"
from .constants import BASE_URL, HEADERS
from .models import (
    TdnetDisclosure,
    TdnetScrapingResult,
    CompanyCode,
    ExchangeCode,
    DisclosureTime,
)
from .parsing import (
    extract_structured_data_from_page,
    extract_pdf_urls_from_page,
    has_next_page,
)
from .services import scrape_tdnet_by_date
from .repository import query_disclosures, upsert_disclosures, get_disclosure
from .artifacts import download_pending_files

__all__ = [
    "BASE_URL",
    "HEADERS",
    "CompanyCode",
    "DisclosureTime",
    "ExchangeCode",
    "TdnetDisclosure",
    "TdnetScrapingResult",
    "extract_pdf_urls_from_page",
    "extract_structured_data_from_page",
    "download_pending_files",
    "get_disclosure",
    "has_next_page",
    "query_disclosures",
    "scrape_tdnet_by_date",
    "upsert_disclosures",
]
