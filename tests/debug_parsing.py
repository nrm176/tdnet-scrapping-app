#!/usr/bin/env python3
"""
Debug script to check what gets parsed from mock HTML.
"""

import sys
import os
from datetime import date
from pathlib import Path

# Add the parent directory to sys.path to import main
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import extract_structured_data_from_page
from bs4 import BeautifulSoup

def debug_parsing():
    """Debug what gets parsed from mock HTML."""
    # Load mock HTML
    tests_dir = Path(__file__).parent
    with open(tests_dir / "mock_tdnet_page.html", "r", encoding="utf-8") as f:
        mock_html = f.read()
    
    soup = BeautifulSoup(mock_html, 'html.parser')
    test_date = date(2025, 10, 21)
    
    # Check table structure
    main_table = soup.find('table', id='main-list-table')
    print(f"Found main table: {main_table is not None}")
    
    if main_table:
        rows = main_table.find_all('tr')
        print(f"Found {len(rows)} rows")
        
        for i, row in enumerate(rows):
            cells = row.find_all('td')
            print(f"Row {i}: {len(cells)} cells")
            for j, cell in enumerate(cells):
                classes = cell.get('class', [])
                text = cell.get_text(strip=True)
                print(f"  Cell {j}: classes={classes}, text='{text[:50]}...'")
    
    # Test extraction
    disclosures = extract_structured_data_from_page(soup, test_date)
    print(f"\nExtracted {len(disclosures)} disclosures")
    
    for i, disclosure in enumerate(disclosures):
        xbrl_info = f" [XBRL: {disclosure.xbrl_available}]" if disclosure.xbrl_available else ""
        print(f"Disclosure {i}: {disclosure.name} ({disclosure.code}) - {disclosure.title[:50]}...{xbrl_info}")

if __name__ == "__main__":
    debug_parsing()