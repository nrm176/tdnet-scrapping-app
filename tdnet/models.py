"""Pydantic models and types for TDnet scraping (aligned with main.py, Pydantic v2)."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import List, Optional, Dict

from pydantic import BaseModel, Field, ConfigDict, computed_field, field_validator, model_validator, HttpUrl
from typing_extensions import Annotated


# Type Aliases using modern Pydantic patterns (match main.py)
CompanyCode = Annotated[str, Field(pattern=r'^[0-9A-Z]+$', description="Company code (e.g., '26530')")]
ExchangeCode = Annotated[str, Field(min_length=1, max_length=10, description="Stock exchange code")]
DisclosureTime = Annotated[str, Field(pattern=r'^\d{1,2}:\d{2}$', description="Disclosure time (HH:MM format)")]


class TdnetDisclosure(BaseModel):
    """TDnet disclosure document (schema mirrors original main.py)."""

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
        """Short SHA-256 hash from key fields (first 16 hex), as in main.py."""
        unique_string = f"{self.code}_{self.disclosure_date}_{str(self.pdf_url)}_{self.time}"
        return sha256(unique_string.encode('utf-8')).hexdigest()[:16]

    @field_validator('code')
    @classmethod
    def validate_company_code(cls, v: str) -> str:
        if not v:
            raise ValueError("Company code cannot be empty")
        normalized = v.strip().upper()
        if not normalized:
            raise ValueError("Company code cannot be just whitespace")
        return normalized

    @field_validator('name', 'title')
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or just whitespace")
        return v.strip()

    @model_validator(mode='after')
    def validate_xbrl_consistency(self):
        if self.xbrl_available and self.xbrl_url is None:
            raise ValueError("xbrl_url must be provided when xbrl_available is True")
        if not self.xbrl_available and self.xbrl_url is not None:
            raise ValueError("xbrl_url should not be provided when xbrl_available is False")
        return self

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid',
        table_name="tdnet_disclosures",
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
    """Complete result of a TDnet scraping operation (matches main.py)."""

    scraping_date: Annotated[date, Field(description="Date that was scraped")]
    total_disclosures: Annotated[int, Field(ge=0, description="Total number of disclosures found")]
    disclosures: Annotated[List[TdnetDisclosure], Field(description="List of disclosure documents")]
    pdf_urls: Annotated[List[HttpUrl], Field(description="List of unique PDF URLs")]

    @computed_field
    @property
    def unique_disclosure_count(self) -> int:
        return len(set(disclosure.id for disclosure in self.disclosures))

    @computed_field
    @property
    def companies_by_exchange(self) -> Dict[str, int]:
        exchange_count: Dict[str, int] = {}
        for disclosure in self.disclosures:
            exchange_count[disclosure.place] = exchange_count.get(disclosure.place, 0) + 1
        return exchange_count

    @field_validator('total_disclosures')
    @classmethod
    def validate_total_disclosures(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Total disclosures cannot be negative")
        return v

    @model_validator(mode='after')
    def validate_consistency(self):
        if self.total_disclosures != len(self.disclosures):
            raise ValueError(
                f"total_disclosures ({self.total_disclosures}) does not match actual disclosure count ({len(self.disclosures)})"
            )

        pdf_url_strs = [str(url) for url in self.pdf_urls]
        if len(pdf_url_strs) != len(set(pdf_url_strs)):
            raise ValueError("PDF URLs must be unique")

        disclosure_urls = {str(d.pdf_url) for d in self.disclosures}
        pdf_urls_set = {str(url) for url in self.pdf_urls}
        missing_urls = disclosure_urls - pdf_urls_set
        if missing_urls:
            raise ValueError(f"Main disclosure PDF URLs missing from pdf_urls list: {missing_urls}")

        return self

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid',
        table_name="tdnet_scraping_results",
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
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.model_dump_json(indent=2, exclude_none=True))

    def get_companies_by_exchange(self, exchange: str) -> List[TdnetDisclosure]:
        return [d for d in self.disclosures if d.place == exchange]

    def get_disclosure_by_id(self, disclosure_id: str) -> Optional[TdnetDisclosure]:
        for disclosure in self.disclosures:
            if disclosure.id == disclosure_id:
                return disclosure
        return None

    def get_unique_disclosure_ids(self) -> List[str]:
        return [disclosure.id for disclosure in self.disclosures]

    def has_duplicate_ids(self) -> bool:
        ids = self.get_unique_disclosure_ids()
        return len(ids) != len(set(ids))

    def get_disclosures_with_xbrl(self) -> List[TdnetDisclosure]:
        return [d for d in self.disclosures if d.xbrl_available]

    def get_company_codes(self) -> List[str]:
        return list(set(d.code for d in self.disclosures))
