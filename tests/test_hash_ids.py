#!/usr/bin/env python3
"""
Test script to demonstrate hash ID functionality for TDnet disclosures.

This script shows:
1. How hash IDs are generated for each disclosure
2. Uniqueness verification
3. Lookup functionality by ID
"""

from datetime import date
from main import TdnetDisclosure, TdnetScrapingResult

def test_hash_ids():
    """Test the hash ID functionality."""
    print("Testing TDnet Hash ID Functionality")
    print("=" * 50)
    
    disclosures = [
        TdnetDisclosure(
            time="15:30",
            code="12345",
            name="テスト株式会社",
            title="業績予想の修正に関するお知らせ",
            pdf_url="https://www.release.tdnet.info/inbs/140120251021001001.pdf",
            xbrl_available=False,
            place="東",
            history="",
            disclosure_date=date(2025, 10, 21),
        ),
        TdnetDisclosure(
            time="16:00",
            code="67890",
            name="サンプル工業株式会社",
            title="決算短信",
            pdf_url="https://www.release.tdnet.info/inbs/140120251021001002.pdf",
            xbrl_available=True,
            xbrl_url="https://www.release.tdnet.info/inbs/081220251021001002.zip",
            place="東",
            history="",
            disclosure_date=date(2025, 10, 21),
        ),
    ]
    result = TdnetScrapingResult(
        scraping_date=date(2025, 10, 21),
        total_disclosures=len(disclosures),
        disclosures=disclosures,
        pdf_urls=[str(disclosure.pdf_url) for disclosure in disclosures],
    )
    
    print(f"Total disclosures: {result.total_disclosures}")
    print(f"Unique disclosure count: {result.unique_disclosure_count}")
    print(f"Any duplicate IDs? {result.has_duplicate_ids()}")
    print()
    
    # Show first few hash IDs
    print("Sample Hash IDs:")
    print("-" * 30)
    for i, disclosure in enumerate(result.disclosures[:5]):
        print(f"{i+1}. ID: {disclosure.id}")
        print(f"   Company: {disclosure.name} ({disclosure.code})")
        print(f"   Time: {disclosure.time}")
        print(f"   Title: {disclosure.title[:50]}...")
        print()
    
    # Test lookup functionality
    print("Testing Lookup by ID:")
    print("-" * 30)
    first_id = result.disclosures[0].id
    found_disclosure = result.get_disclosure_by_id(first_id)
    
    if found_disclosure:
        print(f"Successfully found disclosure with ID: {first_id}")
        print(f"Company: {found_disclosure.name}")
        print(f"Title: {found_disclosure.title}")
    else:
        print("Lookup failed!")
    
    print()
    assert result.total_disclosures == result.unique_disclosure_count
    assert not result.has_duplicate_ids()
    print("Hash ID implementation is working correctly!")

if __name__ == "__main__":
    test_hash_ids()
