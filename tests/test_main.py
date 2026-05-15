#!/usr/bin/env python3
"""
Comprehensive test suite for the TDnet scraping application.

This test suite provides >80% code coverage for main.py using mock HTML data
and comprehensive test scenarios.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock, mock_open
from datetime import date, datetime
from pathlib import Path

# Add the parent directory to sys.path to import main
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import (
    TdnetDisclosure, TdnetScrapingResult, 
    extract_pdf_urls_from_page, 
    extract_structured_data_from_page, scrape_tdnet_by_date,
    has_next_page, main, BASE_URL
)
import requests
from bs4 import BeautifulSoup
from pydantic import ValidationError


class TestTdnetScraping(unittest.TestCase):
    """Comprehensive test suite for TDnet scraping functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test data and mock HTML content."""
        # Load mock HTML files
        tests_dir = Path(__file__).parent
        
        with open(tests_dir / "mock_tdnet_page.html", "r", encoding="utf-8") as f:
            cls.mock_html_with_data = f.read()
            
        with open(tests_dir / "mock_empty_page.html", "r", encoding="utf-8") as f:
            cls.mock_html_empty = f.read()
    
    def setUp(self):
        """Set up test fixtures."""
        self.test_date = date(2025, 10, 21)
        self.sample_disclosure_data = {
            "time": "15:30",
            "code": "12345",
            "name": "テスト株式会社",
            "title": "業績予想の修正に関するお知らせ",
            "pdf_url": "https://www.release.tdnet.info/140120251021001001.pdf",
            "xbrl_available": False,
            "place": "東",
            "history": "",
            "disclosure_date": self.test_date
        }


class TestTdnetDisclosureModel(TestTdnetScraping):
    """Test the TdnetDisclosure Pydantic model."""
    
    def test_disclosure_creation_valid(self):
        """Test creating a valid disclosure."""
        disclosure = TdnetDisclosure(**self.sample_disclosure_data)
        
        self.assertEqual(disclosure.time, "15:30")
        self.assertEqual(disclosure.code, "12345")
        self.assertEqual(disclosure.name, "テスト株式会社")
        self.assertEqual(disclosure.xbrl_available, False)
        self.assertIsNone(disclosure.xbrl_url)
        
    def test_disclosure_with_xbrl(self):
        """Test creating disclosure with XBRL data."""
        data = self.sample_disclosure_data.copy()
        data.update({
            "xbrl_available": True,
            "xbrl_url": "https://www.release.tdnet.info/081220251021001002.zip"
        })
        
        disclosure = TdnetDisclosure(**data)
        self.assertTrue(disclosure.xbrl_available)
        self.assertEqual(str(disclosure.xbrl_url), data["xbrl_url"])
        
    def test_disclosure_hash_id_generation(self):
        """Test hash ID generation."""
        disclosure = TdnetDisclosure(**self.sample_disclosure_data)
        hash_id = disclosure.id
        
        # Hash ID should be 16 characters
        self.assertEqual(len(hash_id), 16)
        self.assertIsInstance(hash_id, str)
        
        # Same data should produce same hash
        disclosure2 = TdnetDisclosure(**self.sample_disclosure_data)
        self.assertEqual(disclosure.id, disclosure2.id)
        
    def test_disclosure_hash_id_uniqueness(self):
        """Test that different data produces different hash IDs."""
        disclosure1 = TdnetDisclosure(**self.sample_disclosure_data)
        
        data2 = self.sample_disclosure_data.copy()
        data2["time"] = "16:00"  # Change time
        disclosure2 = TdnetDisclosure(**data2)
        
        self.assertNotEqual(disclosure1.id, disclosure2.id)
        
    def test_xbrl_validation_missing_url(self):
        """Test validation when XBRL is available but URL is missing."""
        data = self.sample_disclosure_data.copy()
        data["xbrl_available"] = True
        # xbrl_url is None by default
        
        with self.assertRaises(ValidationError):
            TdnetDisclosure(**data)
            
    def test_disclosure_invalid_url(self):
        """Test validation with invalid URLs."""
        data = self.sample_disclosure_data.copy()
        data["pdf_url"] = "not-a-valid-url"
        
        with self.assertRaises(ValidationError):
            TdnetDisclosure(**data)


class TestTdnetScrapingResult(TestTdnetScraping):
    """Test the TdnetScrapingResult model and its methods."""
    
    def setUp(self):
        """Set up test result with sample disclosures."""
        super().setUp()
        
        # Create sample disclosures with unique PDF URLs
        self.disclosures = [
            TdnetDisclosure(**self.sample_disclosure_data),
            TdnetDisclosure(**{
                **self.sample_disclosure_data,
                "code": "67890",
                "name": "サンプル工業株式会社",
                "time": "16:00",
                "pdf_url": "https://www.release.tdnet.info/140120251021001002.pdf",
                "xbrl_available": True,
                "xbrl_url": "https://www.release.tdnet.info/081220251021001002.zip"
            }),
            TdnetDisclosure(**{
                **self.sample_disclosure_data,
                "code": "99999",
                "name": "テスト札幌",
                "time": "09:00",
                "pdf_url": "https://www.release.tdnet.info/140120251021001003.pdf",
                "place": "札"
            })
        ]
        
        self.pdf_urls = [str(d.pdf_url) for d in self.disclosures]
        
        self.result = TdnetScrapingResult(
            scraping_date=self.test_date,
            total_disclosures=len(self.disclosures),
            disclosures=self.disclosures,
            pdf_urls=self.pdf_urls
        )
    
    def test_result_creation(self):
        """Test creating scraping result."""
        self.assertEqual(self.result.scraping_date, self.test_date)
        self.assertEqual(self.result.total_disclosures, 3)
        self.assertEqual(len(self.result.disclosures), 3)
        
    def test_unique_disclosure_count(self):
        """Test unique disclosure count computed field."""
        self.assertEqual(self.result.unique_disclosure_count, 3)
        
    def test_get_companies_by_exchange(self):
        """Test filtering by exchange."""
        tokyo_companies = self.result.get_companies_by_exchange("東")
        self.assertEqual(len(tokyo_companies), 2)
        
        sapporo_companies = self.result.get_companies_by_exchange("札")
        self.assertEqual(len(sapporo_companies), 1)
        self.assertEqual(sapporo_companies[0].name, "テスト札幌")
        
    def test_get_disclosure_by_id(self):
        """Test finding disclosure by hash ID."""
        first_id = self.disclosures[0].id
        found = self.result.get_disclosure_by_id(first_id)
        
        self.assertIsNotNone(found)
        self.assertEqual(found.id, first_id)
        
        # Test with non-existent ID
        not_found = self.result.get_disclosure_by_id("nonexistent")
        self.assertIsNone(not_found)
        
    def test_get_unique_disclosure_ids(self):
        """Test getting unique IDs."""
        ids = self.result.get_unique_disclosure_ids()
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)  # All should be unique
        
    def test_has_duplicate_ids(self):
        """Test duplicate ID detection."""
        self.assertFalse(self.result.has_duplicate_ids())
        
    @patch("builtins.open", new_callable=mock_open)
    def test_save_to_json_file(self, mock_file):
        """Test saving to JSON file."""
        filepath = "/tmp/test.json"
        self.result.save_to_json_file(filepath)
        
        mock_file.assert_called_once_with(filepath, 'w', encoding='utf-8')
        handle = mock_file()
        handle.write.assert_called_once()


class TestUtilityFunctions(TestTdnetScraping):
    """Test utility functions."""
    
    def test_construct_tdnet_url(self):
        """Test URL construction logic (inline)."""
        # Test URL construction similar to main.py logic
        date_str = self.test_date.strftime("%Y%m%d")
        
        # Page 1 URL
        url1 = f"{BASE_URL}/inbs/I_list_001_{date_str}.html"
        expected1 = "https://www.release.tdnet.info/inbs/I_list_001_20251021.html"
        self.assertEqual(url1, expected1)
        
        # Page 2 URL
        page_num_str = f"{2:03d}"
        url2 = f"{BASE_URL}/inbs/I_list_{page_num_str}_{date_str}.html"
        expected2 = "https://www.release.tdnet.info/inbs/I_list_002_20251021.html"
        self.assertEqual(url2, expected2)
        
    def test_extract_pdf_urls_from_page(self):
        """Test PDF URL extraction from mock HTML."""
        soup = BeautifulSoup(self.mock_html_with_data, 'html.parser')
        urls = extract_pdf_urls_from_page(soup)
        
        expected_urls = [
            "https://www.release.tdnet.info/140120251021001001.pdf",
            "https://www.release.tdnet.info/140120251021001002.pdf",
            "https://www.release.tdnet.info/140120251021001003.pdf",
            "https://www.release.tdnet.info/140120251021001005.pdf",
            "https://www.release.tdnet.info/140120251021001006.pdf"
        ]
        
        self.assertEqual(urls, expected_urls)
        
    def test_extract_pdf_urls_empty_page(self):
        """Test PDF URL extraction from empty page."""
        soup = BeautifulSoup(self.mock_html_empty, 'html.parser')
        urls = extract_pdf_urls_from_page(soup)
        
        self.assertEqual(urls, [])
        
    def test_extract_structured_data_from_page(self):
        """Test structured data extraction from mock HTML."""
        soup = BeautifulSoup(self.mock_html_with_data, 'html.parser')
        disclosures = extract_structured_data_from_page(soup, self.test_date)
        
        self.assertEqual(len(disclosures), 5)
        
        # Test first disclosure
        first = disclosures[0]
        self.assertEqual(first.time, "15:30")
        self.assertEqual(first.code, "12345")
        self.assertEqual(first.name, "テスト株式会社")
        self.assertEqual(first.title, "業績予想の修正に関するお知らせ")
        self.assertFalse(first.xbrl_available)
        self.assertIsNone(first.xbrl_url)
        
        # Test disclosure with XBRL
        second = disclosures[1]
        self.assertEqual(second.code, "67890")
        self.assertTrue(second.xbrl_available)
        self.assertEqual(str(second.xbrl_url), "https://www.release.tdnet.info/081220251021001002.zip")
        
        # Test different exchange
        fourth = disclosures[3]
        self.assertEqual(fourth.place, "札")
        
    def test_extract_structured_data_empty_page(self):
        """Test structured data extraction from empty page."""
        soup = BeautifulSoup(self.mock_html_empty, 'html.parser')
        disclosures = extract_structured_data_from_page(soup, self.test_date)
        
        self.assertEqual(len(disclosures), 0)
        
    def test_has_next_page(self):
        """Test next page detection."""
        # Test with empty page (no next page)
        soup = BeautifulSoup(self.mock_html_empty, 'html.parser')
        self.assertFalse(has_next_page(soup))
        
        # Test with page that has data (should return True since our mock now has pagination)
        soup = BeautifulSoup(self.mock_html_with_data, 'html.parser')
        self.assertTrue(has_next_page(soup))


class TestScrapingIntegration(TestTdnetScraping):
    """Test the main scraping integration."""
    
    @patch('main.requests.Session')
    def test_scrape_tdnet_by_date_success(self, mock_session_class):
        """Test successful scraping with mock responses."""
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        # Mock first page response
        mock_response1 = MagicMock()
        mock_response1.status_code = 200
        mock_response1.text = self.mock_html_with_data
        mock_response1.raise_for_status = MagicMock()
        
        # Mock second page response (empty)
        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.text = self.mock_html_empty
        mock_response2.raise_for_status = MagicMock()
        
        mock_session.get.side_effect = [mock_response1, mock_response2]
        
        result = scrape_tdnet_by_date(self.test_date)
        
        self.assertIsInstance(result, TdnetScrapingResult)
        self.assertEqual(result.scraping_date, self.test_date)
        self.assertEqual(result.total_disclosures, 5)
        self.assertEqual(len(result.disclosures), 5)
        
        # Verify requests were made
        self.assertEqual(mock_session.get.call_count, 2)
        
    @patch('main.requests.Session')
    def test_scrape_tdnet_by_date_http_error(self, mock_session_class):
        """Test scraping with HTTP error."""
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "<html></html>"  # Provide actual HTML string
        mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_session.get.return_value = mock_response
        
        # Should return empty result, not raise exception
        result = scrape_tdnet_by_date(self.test_date)
        self.assertEqual(result.total_disclosures, 0)
            
    @patch('main.requests.Session')
    def test_scrape_tdnet_by_date_connection_error(self, mock_session_class):
        """Test scraping with connection error."""
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.get.side_effect = requests.ConnectionError("Connection failed")
        
        # Should return empty result, not raise exception
        result = scrape_tdnet_by_date(self.test_date)
        self.assertEqual(result.total_disclosures, 0)


class TestMainFunction(TestTdnetScraping):
    """Test the main function and CLI interface."""
    
    @patch('main.scrape_tdnet_by_date')
    @patch('sys.argv', ['main.py', '--date=2025-10-21', '--output-format=structured'])
    def test_main_structured_output(self, mock_scrape):
        
        # Create mock result
        mock_disclosures = [TdnetDisclosure(**self.sample_disclosure_data)]
        mock_result = TdnetScrapingResult(
            scraping_date=self.test_date,
            total_disclosures=1,
            disclosures=mock_disclosures,
            pdf_urls=["https://www.release.tdnet.info/140120251021001001.pdf"]
        )
        mock_scrape.return_value = mock_result
        
        with patch('builtins.print') as mock_print:
            main()
            
        mock_scrape.assert_called_once_with(self.test_date)
        mock_print.assert_called()
        
    @patch('sys.argv', ['main.py', '--date=invalid-date'])
    def test_main_invalid_date(self):
        """Test main function with invalid date."""
        with patch('sys.exit') as mock_exit:
            main()
            mock_exit.assert_called_with(1)
            
    @patch('sys.argv', ['main.py'])
    def test_main_missing_date(self):
        """Test main function with missing date argument."""
        # argparse will raise SystemExit when required arguments are missing
        with self.assertRaises(SystemExit) as context:
            main()
        # argparse exits with code 2 for missing required arguments
        self.assertEqual(context.exception.code, 2)
            
    @patch('main.scrape_tdnet_by_date')
    @patch('sys.argv', ['main.py', '--date=2025-10-21', '--json'])
    def test_main_json_output(self, mock_scrape):
        
        mock_disclosures = [TdnetDisclosure(**self.sample_disclosure_data)]
        mock_result = TdnetScrapingResult(
            scraping_date=self.test_date,
            total_disclosures=1,
            disclosures=mock_disclosures,
            pdf_urls=["https://www.release.tdnet.info/140120251021001001.pdf"]
        )
        mock_scrape.return_value = mock_result
        
        with patch('builtins.print') as mock_print:
            main()
            
        mock_scrape.assert_called_once_with(self.test_date)
        # Should print JSON
        mock_print.assert_called()
        
    @patch('main.scrape_tdnet_by_date')
    @patch('sys.argv', ['main.py', '--date=2025-10-21', '--output-format=urls'])
    def test_main_urls_output(self, mock_scrape):
        
        mock_disclosures = [TdnetDisclosure(**self.sample_disclosure_data)]
        mock_result = TdnetScrapingResult(
            scraping_date=self.test_date,
            total_disclosures=1,
            disclosures=mock_disclosures,
            pdf_urls=["https://www.release.tdnet.info/140120251021001001.pdf"]
        )
        mock_scrape.return_value = mock_result
        
        with patch('builtins.print') as mock_print:
            main()
            
        mock_scrape.assert_called_once_with(self.test_date)
        mock_print.assert_called()


class TestErrorHandling(TestTdnetScraping):
    """Test error handling scenarios."""
    
    def test_disclosure_model_validation_errors(self):
        """Test various validation errors in disclosure model."""
        # Missing required field
        with self.assertRaises(ValidationError):
            TdnetDisclosure()
            
        # Invalid date type
        invalid_data = self.sample_disclosure_data.copy()
        invalid_data["disclosure_date"] = "not-a-date"
        with self.assertRaises(ValidationError):
            TdnetDisclosure(**invalid_data)
            
    def test_malformed_html_handling(self):
        """Test handling of malformed HTML."""
        malformed_html = "<html><body><table><tr><td>incomplete"
        soup = BeautifulSoup(malformed_html, 'html.parser')
        
        # Should handle gracefully and return empty list
        urls = extract_pdf_urls_from_page(soup)
        self.assertEqual(urls, [])
        
        disclosures = extract_structured_data_from_page(soup, self.test_date)
        self.assertEqual(disclosures, [])


if __name__ == '__main__':
    # Run tests with coverage if available
    try:
        import coverage
        cov = coverage.Coverage()
        cov.start()
        
        unittest.main(exit=False, verbosity=2)
        
        cov.stop()
        cov.save()
        
        print("\nCoverage Report:")
        cov.report(show_missing=True)
        
    except ImportError:
        # Run without coverage
        unittest.main(verbosity=2)