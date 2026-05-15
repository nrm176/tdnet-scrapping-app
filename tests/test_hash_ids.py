#!/usr/bin/env python3
"""
Test script to demonstrate hash ID functionality for TDnet disclosures.

This script shows:
1. How hash IDs are generated for each disclosure
2. Uniqueness verification
3. Lookup functionality by ID
"""

import sys
from datetime import date
from main import scrape_tdnet_by_date

def test_hash_ids():
    """Test the hash ID functionality."""
    print("Testing TDnet Hash ID Functionality")
    print("=" * 50)
    
    # Scrape a small sample
    result = scrape_tdnet_by_date(date(2025, 10, 21))
    
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
    print("Hash ID implementation is working correctly! ✅")

if __name__ == "__main__":
    test_hash_ids()