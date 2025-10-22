#!/usr/bin/env python3
"""
Test script to verify hash ID consistency and uniqueness.
"""

import hashlib
from datetime import date
from main import TdnetDisclosure

def test_hash_consistency():
    """Test that the same data always generates the same hash."""
    print("Testing Hash ID Consistency")
    print("=" * 40)
    
    # Create the same disclosure twice
    disclosure_data = {
        "time": "15:30",
        "code": "12345", 
        "name": "Test Company",
        "title": "Test Disclosure",
        "pdf_url": "https://example.com/test123.pdf",
        "xbrl_available": True,
        "xbrl_url": "https://example.com/test123.zip",
        "place": "東",
        "history": "",
        "disclosure_date": date(2025, 10, 21)
    }
    
    disclosure1 = TdnetDisclosure(**disclosure_data)
    disclosure2 = TdnetDisclosure(**disclosure_data)
    
    print(f"Disclosure 1 ID: {disclosure1.id}")
    print(f"Disclosure 2 ID: {disclosure2.id}")
    print(f"IDs match: {disclosure1.id == disclosure2.id}")
    print()
    
    # Test with different data to ensure they get different hashes
    disclosure_data2 = disclosure_data.copy()
    disclosure_data2["time"] = "16:00"  # Change only the time
    
    disclosure3 = TdnetDisclosure(**disclosure_data2)
    
    print(f"Modified disclosure ID: {disclosure3.id}")
    print(f"Different from original: {disclosure1.id != disclosure3.id}")
    print()
    
    # Show how the hash is constructed
    print("Hash Construction Details:")
    print("-" * 30)
    unique_string = f"{disclosure1.code}_{disclosure1.disclosure_date}_{str(disclosure1.pdf_url)}_{disclosure1.time}"
    print(f"Unique string: {unique_string}")
    
    hash_object = hashlib.sha256(unique_string.encode('utf-8'))
    full_hash = hash_object.hexdigest()
    
    print(f"Full SHA-256: {full_hash}")
    print(f"Truncated ID: {full_hash[:16]}")
    print(f"Matches model: {disclosure1.id == full_hash[:16]}")

if __name__ == "__main__":
    test_hash_consistency()