import argparse
import hashlib
import logging
import sys
from datetime import datetime, date
from typing import List, Optional, Dict, Any, Annotated
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field, HttpUrl, model_validator, ConfigDict, computed_field, field_validator

# Configure logging for clear and informative output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)

# Define constants for the target website
BASE_URL = "https://www.release.tdnet.info"

# Set a user-agent to mimic a standard web browser
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}


# Type Aliases using modern Pydantic patterns
CompanyCode = Annotated[str, Field(pattern=r'^[0-9A-Z]+$', description="Company code (e.g., '26530')")]
ExchangeCode = Annotated[str, Field(min_length=1, max_length=10, description="Stock exchange code")]
DisclosureTime = Annotated[str, Field(pattern=r'^\d{1,2}:\d{2}$', description="Disclosure time (HH:MM format)")]

# Pydantic Models for Data Structure
class TdnetDisclosure(BaseModel):
    """
    Modern Pydantic V2+ model representing a TDnet disclosure document.
    
    This model uses advanced Pydantic features including:
    - Annotated types for better validation
    - Field validators with clear error messages
    - Computed fields for derived properties
    - Modern configuration patterns
    """
    time: DisclosureTime
    code: CompanyCode
    name: Annotated[str, Field(min_length=1, description="Company name")]
    title: Annotated[str, Field(min_length=1, description="Document title")]
    pdf_url: Annotated[HttpUrl, Field(description="Direct URL to the PDF document")]
    xbrl_available: Annotated[bool, Field(default=False, description="Whether XBRL data is available")]
    xbrl_url: Annotated[Optional[HttpUrl], Field(default=None, description="URL to XBRL file if available")]
    place: ExchangeCode
    history: Annotated[str, Field(default="", description="Update history information")]
    disclosure_date: Annotated[date, Field(description="Date of disclosure")]
    
    @computed_field
    @property
    def id(self) -> str:
        """
        Generate a unique hash ID for this disclosure record.
        
        The hash is computed from key attributes that uniquely identify a disclosure:
        - Company code
        - Disclosure date
        - PDF URL (contains unique document ID)
        - Time (to handle multiple disclosures from same company on same day)
        
        Returns:
            str: SHA-256 hash as hexadecimal string (first 16 characters for readability)
        """
        # Create a string combining the key identifying attributes
        unique_string = f"{self.code}_{self.disclosure_date}_{str(self.pdf_url)}_{self.time}"
        
        # Generate SHA-256 hash and return first 16 characters for readability
        hash_object = hashlib.sha256(unique_string.encode('utf-8'))
        return hash_object.hexdigest()[:16]
    
    @field_validator('code')
    @classmethod
    def validate_company_code(cls, v: str) -> str:
        """Validate company code format and normalize it."""
        if not v:
            raise ValueError("Company code cannot be empty")
        # Normalize code (remove extra spaces, convert to uppercase)
        normalized = v.strip().upper()
        if not normalized:
            raise ValueError("Company code cannot be just whitespace")
        return normalized
    
    @field_validator('name', 'title')
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        """Validate that name and title are not empty or just whitespace."""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or just whitespace")
        return v.strip()
    
    @model_validator(mode='after')
    def validate_xbrl_consistency(self):
        """Ensure XBRL URL is provided when XBRL is available, and vice versa."""
        if self.xbrl_available and self.xbrl_url is None:
            raise ValueError("xbrl_url must be provided when xbrl_available is True")
        if not self.xbrl_available and self.xbrl_url is not None:
            raise ValueError("xbrl_url should not be provided when xbrl_available is False")
        return self
    
    # Modern Pydantic V2+ configuration
    model_config = ConfigDict(
        # Strict validation for better type safety
        str_strip_whitespace=True,
        validate_assignment=True,
        # Future-proof configuration
        extra='forbid',
        # Database integration metadata
        table_name="tdnet_disclosures",
        # JSON schema generation
        json_schema_extra={
            "example": {
                "time": "19:00",
                "code": "26530",
                "name": "イオン九州",
                "title": "完全子会社の吸収合併に関するお知らせ",
                "pdf_url": "https://www.release.tdnet.info/140120251020576212.pdf",
                "xbrl_available": False,
                "xbrl_url": None,
                "place": "東",
                "history": "",
                "disclosure_date": "2025-10-21"
            }
        }
    )


class TdnetScrapingResult(BaseModel):
    """
    Modern Pydantic V2+ model representing the complete result of a TDnet scraping operation.
    
    Uses advanced features:
    - Annotated types for better validation
    - Field validators for data consistency
    - Computed properties for derived data
    - Comprehensive model validation
    """
    scraping_date: Annotated[date, Field(description="Date that was scraped")]
    total_disclosures: Annotated[int, Field(ge=0, description="Total number of disclosures found")]
    disclosures: Annotated[List[TdnetDisclosure], Field(description="List of disclosure documents")]
    pdf_urls: Annotated[List[HttpUrl], Field(description="List of unique PDF URLs")]
    
    @computed_field
    @property
    def unique_disclosure_count(self) -> int:
        """Count of unique disclosure IDs (should match total_disclosures)."""
        return len(set(disclosure.id for disclosure in self.disclosures))
    
    @computed_field
    @property
    def companies_by_exchange(self) -> Dict[str, int]:
        """Count of disclosures by exchange."""
        exchange_count: Dict[str, int] = {}
        for disclosure in self.disclosures:
            exchange_count[disclosure.place] = exchange_count.get(disclosure.place, 0) + 1
        return exchange_count
    
    @field_validator('total_disclosures')
    @classmethod
    def validate_total_disclosures(cls, v: int) -> int:
        """Validate that total_disclosures is non-negative."""
        if v < 0:
            raise ValueError("Total disclosures cannot be negative")
        return v
    
    @model_validator(mode='after')
    def validate_consistency(self):
        """Ensure data consistency across all fields."""
        # Validate that total_disclosures matches actual count
        if self.total_disclosures != len(self.disclosures):
            raise ValueError(f"total_disclosures ({self.total_disclosures}) does not match "
                           f"actual disclosure count ({len(self.disclosures)})")
        
        # Validate that all PDF URLs are unique
        pdf_url_strs = [str(url) for url in self.pdf_urls]
        if len(pdf_url_strs) != len(set(pdf_url_strs)):
            raise ValueError("PDF URLs must be unique")
        
        # Validate that main disclosure PDF URLs are included in the pdf_urls list
        # Note: pdf_urls may contain additional URLs (supplementary docs, etc.)
        # that are not main disclosure URLs, which is expected behavior
        disclosure_urls = {str(d.pdf_url) for d in self.disclosures}
        pdf_urls_set = {str(url) for url in self.pdf_urls}
        missing_urls = disclosure_urls - pdf_urls_set
        if missing_urls:
            raise ValueError(f"Main disclosure PDF URLs missing from pdf_urls list: {missing_urls}")
        
        return self
    
    # Modern Pydantic V2+ configuration
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid',
        # Database integration metadata
        table_name="tdnet_scraping_results",
        # JSON schema with example
        json_schema_extra={
            "example": {
                "scraping_date": "2025-10-21",
                "total_disclosures": 2,
                "disclosures": [
                    {
                        "time": "19:00",
                        "code": "26530",
                        "name": "イオン九州",
                        "title": "完全子会社の吸収合併に関するお知らせ",
                        "pdf_url": "https://www.release.tdnet.info/140120251020576212.pdf",
                        "xbrl_available": False,
                        "place": "東",
                        "disclosure_date": "2025-10-21"
                    }
                ],
                "pdf_urls": ["https://www.release.tdnet.info/140120251020576212.pdf"]
            }
        }
    )
    
    def save_to_json_file(self, filepath: str) -> None:
        """Save the scraping result to a JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.model_dump_json(indent=2, exclude_none=True))
    
    def get_companies_by_exchange(self, exchange: str) -> List[TdnetDisclosure]:
        """Get all disclosures for a specific exchange."""
        return [d for d in self.disclosures if d.place == exchange]
    
    def get_disclosure_by_id(self, disclosure_id: str) -> Optional[TdnetDisclosure]:
        """Get a specific disclosure by its hash ID."""
        for disclosure in self.disclosures:
            if disclosure.id == disclosure_id:
                return disclosure
        return None
    
    def get_unique_disclosure_ids(self) -> List[str]:
        """Get a list of all unique disclosure IDs."""
        return [disclosure.id for disclosure in self.disclosures]
    
    def has_duplicate_ids(self) -> bool:
        """Check if there are any duplicate IDs in the dataset."""
        ids = self.get_unique_disclosure_ids()
        return len(ids) != len(set(ids))
    
    def get_disclosures_with_xbrl(self) -> List[TdnetDisclosure]:
        """Get all disclosures that have XBRL data available."""
        return [d for d in self.disclosures if d.xbrl_available]
    
    def get_company_codes(self) -> List[str]:
        """Get unique company codes from all disclosures."""
        return list(set(d.code for d in self.disclosures))


def extract_structured_data_from_page(soup: BeautifulSoup, disclosure_date: date) -> List[TdnetDisclosure]:
    """
    Parses a BeautifulSoup object to extract structured data from each disclosure row.

    Args:
        soup: A BeautifulSoup object representing the parsed HTML of a results page.
        disclosure_date: The date of the disclosures being scraped.

    Returns:
        A list of TdnetDisclosure objects containing structured data for each disclosure.
    """
    structured_data = []
    
    # Find all table rows in the main list table
    main_table = soup.find('table', id='main-list-table')
    if not main_table:
        logging.warning("Could not find main-list-table")
        return structured_data
    
    rows = main_table.find_all('tr')
    for row in rows:
        cells = row.find_all('td')
        if len(cells) < 6:  # Skip if not enough columns
            continue
            
        # Extract data from each cell by looking for "kj" class attributes
        row_data = {}
        
        for cell in cells:
            cell_classes = cell.get('class', [])
            for class_name in cell_classes:
                if class_name.startswith('kj'):
                    # Extract the field name (e.g., 'kjTime' -> 'time')
                    field_name = class_name[2:].lower()  # Remove 'kj' prefix and lowercase
                    
                    if class_name == 'kjTitle':
                        # Special handling for title - extract text and FIRST PDF link only
                        # Also check for XBRL links in the same cell
                        link_tags = cell.find_all('a')
                        row_data['xbrl_available'] = False  # Default to False
                        
                        for link_tag in link_tags:
                            href = link_tag.get('href', '')
                            if href.endswith('.pdf') and 'pdf_url' not in row_data:
                                # Only take the FIRST PDF link as the main disclosure
                                row_data['title'] = link_tag.get_text(strip=True)
                                row_data['pdf_url'] = urljoin(BASE_URL, href)
                            elif href.endswith('.zip'):
                                row_data['xbrl_url'] = urljoin(BASE_URL, href)
                                row_data['xbrl_available'] = True
                        
                        # If no PDF link found but we have text, use the cell text as title
                        if 'title' not in row_data:
                            row_data['title'] = cell.get_text(strip=True)
                    elif class_name == 'kjXbrl':
                        # Special handling for XBRL - check if there's a download link
                        xbrl_link = cell.find('a')
                        if xbrl_link and xbrl_link.get('href', '').endswith('.zip'):
                            row_data['xbrl_url'] = urljoin(BASE_URL, xbrl_link['href'])
                            row_data['xbrl_available'] = True
                        else:
                            row_data['xbrl_available'] = False
                    elif class_name == 'kjHistroy':
                        # Map kjHistroy to 'history' field
                        row_data['history'] = cell.get_text(strip=True)
                    elif class_name == 'kjCompany':
                        # Map kjCompany to 'name' field
                        row_data['name'] = cell.get_text(strip=True)
                    else:
                        # For other fields, just extract the text content
                        row_data[field_name] = cell.get_text(strip=True)
                    
                    break  # Only process the first "kj" class found
        
        # Only create disclosure objects that have meaningful data
        if 'time' in row_data and 'code' in row_data and 'pdf_url' in row_data:
            try:
                # Add the disclosure date
                row_data['disclosure_date'] = disclosure_date
                
                # Create TdnetDisclosure object
                disclosure = TdnetDisclosure(**row_data)
                structured_data.append(disclosure)
            except Exception as e:
                logging.warning(f"Failed to create TdnetDisclosure object: {e}")
                logging.debug(f"Row data: {row_data}")
    
    logging.info(f"Extracted structured data for {len(structured_data)} disclosures on this page.")
    return structured_data


def extract_pdf_urls_from_page(soup: BeautifulSoup) -> List[str]:
    """
    Parses a BeautifulSoup object to find and extract all PDF URLs.

    Args:
        soup: A BeautifulSoup object representing the parsed HTML of a results page.

    Returns:
        A list of absolute URLs pointing to PDF files.
    """
    urls = []
    # Find all table rows in the main list table
    main_table = soup.find('table', id='main-list-table')
    if not main_table:
        logging.warning("Could not find main-list-table")
        return urls
    
    rows = main_table.find_all('tr')
    for row in rows:
        cells = row.find_all('td')
        if len(cells) >= 4:  # Ensure we have enough columns
            # The PDF link is in the 4th column (kjTitle column, index 3)
            title_cell = cells[3]
            if title_cell and 'kjTitle' in title_cell.get('class', []):
                link_tag = title_cell.find('a')
                if link_tag and 'href' in link_tag.attrs:
                    href = link_tag['href']
                    # Check if it's a PDF file
                    if href.endswith('.pdf'):
                        absolute_url = urljoin(BASE_URL, href)
                        urls.append(absolute_url)
    
    logging.info(f"Found {len(urls)} PDF URLs on this page.")
    return urls


def has_next_page(soup: BeautifulSoup) -> bool:
    """
    Checks if a "Next" button exists on the page, indicating pagination.

    The "Next" link is identified by the pager-R div with onclick attribute.

    Args:
        soup: A BeautifulSoup object of the results page.

    Returns:
        True if a next page link is found, False otherwise.
    """
    # Find the pagination control div with class pager-R
    next_page_div = soup.find('div', class_='pager-R')
    if not next_page_div:
        return False

    # Check if it has an onclick attribute (indicating it's clickable/active)
    onclick_attr = next_page_div.get('onclick', '')
    # If onclick contains a filename, there's a next page
    return 'I_list_' in onclick_attr and '.html' in onclick_attr


def scrape_tdnet_by_date(target_date: date) -> TdnetScrapingResult:
    """
    Scrapes all disclosure PDF URLs and structured data for a specific date using direct URL access.

    Args:
        target_date: The date for which to scrape the disclosures.

    Returns:
        A TdnetScrapingResult object containing all scraped data.
    """
    all_found_urls = []
    all_structured_data = []
    current_page = 1
    
    # Format date as YYYYMMDD
    date_str = target_date.strftime("%Y%m%d")
    
    # Use a requests.Session() object to persist headers and cookies
    with requests.Session() as session:
        session.headers.update(HEADERS)
        
        while True:
            # Construct the URL for the specific date and page
            if current_page == 1:
                page_url = f"{BASE_URL}/inbs/I_list_001_{date_str}.html"
            else:
                # For subsequent pages, use format: I_list_00X_YYYYMMDD.html
                page_num_str = f"{current_page:03d}"  # Zero-pad to 3 digits
                page_url = f"{BASE_URL}/inbs/I_list_{page_num_str}_{date_str}.html"

            logging.info(f"Requesting data for {target_date.strftime('%Y-%m-%d')}, page {current_page}...")
            logging.info(f"URL: {page_url}")
            
            try:
                response = session.get(page_url)
                # Check if we get a 404 or other error for non-existent pages
                if response.status_code == 404:
                    logging.info(f"Page {current_page} not found (404). Reached end of results.")
                    break
                # Raise an HTTPError if the HTTP request returned an unsuccessful status code
                response.raise_for_status()
                # Ensure the content is decoded correctly
                response.encoding = 'utf-8' 

            except requests.exceptions.RequestException as e:
                logging.error(f"Failed to retrieve page {current_page}. Error: {e}")
                break

            soup = BeautifulSoup(response.text, 'lxml')
            
            # Check for a "no results" message or if no main table exists
            main_table = soup.find('table', id='main-list-table')
            if (soup.find(string='検索条件に該当するデータが見つかりません。') or 
                not main_table or 
                not main_table.find_all('tr')):
                if current_page == 1:
                    logging.warning(f"No data found for the date: {target_date.strftime('%Y-%m-%d')}")
                else:
                    logging.info("Reached the end of the results.")
                break
            
            # Extract both PDF URLs and structured data
            page_urls = extract_pdf_urls_from_page(soup)
            page_structured_data = extract_structured_data_from_page(soup, target_date)
            
            all_found_urls.extend(page_urls)
            all_structured_data.extend(page_structured_data)

            if not has_next_page(soup):
                logging.info("Last page reached. Concluding scrape for this date.")
                break
            
            current_page += 1

    # Return unique URLs and create TdnetScrapingResult
    unique_urls = sorted(list(set(all_found_urls)))
    
    return TdnetScrapingResult(
        scraping_date=target_date,
        total_disclosures=len(all_structured_data),
        disclosures=all_structured_data,
        pdf_urls=unique_urls
    )


def main():
    """
    Main function to parse arguments and orchestrate the scraping process.
    """
    parser = argparse.ArgumentParser(
        description="Scrape TDnet for disclosure URLs and structured data on a specific date."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="The date to scrape in YYYY-MM-DD format.",
        type=str
    )
    parser.add_argument(
        "--output-format",
        choices=["urls", "structured", "both"],
        default="urls",
        help="Output format: 'urls' for PDF URLs only, 'structured' for structured data, 'both' for both"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured data in JSON format"
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
                    # Use Pydantic's built-in JSON serialization
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