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
