# TDnet Scraping Application

Installable Python package providing a CLI and typed models for scraping Japanese TDnet disclosures.

![CI](https://github.com/nrm176/tdnet-scrapping-app/actions/workflows/ci.yml/badge.svg?branch=main)

## Quick install

```bash
pip install tdnet-scraper
```

## CLI usage

```bash
tdnet --date 2025-10-21 --output-format structured
# or JSON
tdnet --date 2025-10-21 --json
```

Note: Local workflow via `python main.py` remains supported for backward compatibility and tests.

## CI and distribution

This repo includes GitHub Actions to test and build distributable packages you can install locally:

- CI (`.github/workflows/ci.yml`):
    - Runs tests on Python 3.9–3.13 on pushes and PRs to `main`.
    - Builds sdist/wheel and uploads them as run artifacts.

- Release artifacts (`.github/workflows/publish.yml`):
    - On GitHub Release publish: builds and attaches `dist/*.whl` and `dist/*.tar.gz` to the Release.

### Install via Release assets

1) Go to the GitHub repository Releases page and download the wheel (e.g., `tdnet_scraper-<version>-py3-none-any.whl`).
2) Install it:

```bash
pip install /path/to/tdnet_scraper-<version>-py3-none-any.whl
```

### Install via CI artifact

1) Open the latest successful CI run on `main`.
2) Download the `dist` artifact.
3) Install the wheel or sdist:

```bash
pip install dist/tdnet_scraper-<version>-py3-none-any.whl
# or
pip install dist/tdnet_scraper-<version>.tar.gz
```

A modern, robust Python application for scraping Japanese corporate disclosure data from TDnet (Timely Disclosure Network). Built with cutting-edge **Pydantic V2+** patterns and comprehensive testing infrastructure.

## 🏗️ Architecture Overview

### Core Components
- **Modern Pydantic Models**: Type-safe data structures with advanced validation
- **Web Scraping Engine**: BeautifulSoup-based HTML parsing with robust error handling
- **CLI Interface**: Argparse-based command-line interface with multiple output formats
- **Testing Infrastructure**: 89% test coverage with comprehensive mock data
- **Hash ID System**: SHA-256 based unique identifiers for database integration

### Technology Stack
- **Python 3.10+** with modern type hints and Annotated types
- **Pydantic 2.12.3** with V2+ patterns (future V3-ready)
- **BeautifulSoup4** for HTML parsing with lxml backend
- **Requests** with session management and proper headers
- **Pytest** with coverage reporting and comprehensive mocking

## 🚀 Features

### Core Functionality
- **Date-based Scraping**: Extract disclosures for any specific date with pagination support
- **Advanced Data Validation**: Pydantic V2+ models with field validators and model validators
- **XBRL Support**: Automatic detection and handling of XBRL financial data files
- **Unique Hash IDs**: SHA-256 based identifiers ensuring data consistency across scraping runs
- **Multiple Output Formats**: JSON, structured text, or PDF URLs with consistent formatting

### Modern Pydantic Features
- **Annotated Types**: Type-safe field definitions with pattern validation
- **Field Validators**: Custom validation logic for data normalization and integrity
- **Model Validators**: Cross-field validation ensuring data consistency
- **Computed Fields**: Dynamic properties calculated from model data
- **JSON Schema Generation**: API-ready documentation with examples
- **Database Integration Ready**: Metadata for ORM integration

### Quality Assurance
- **89% Test Coverage**: Comprehensive test suite with realistic mock data
- **Type Safety**: Full type hints with mypy compatibility
- **Error Handling**: Robust exception handling with informative error messages
- **Data Integrity**: Multi-level validation preventing corrupted data
- **Performance Optimized**: Efficient parsing and minimal memory footprint

## 📋 Requirements

- Python 3.8+
- Virtual environment (recommended)

## 🛠️ Installation (from source)

1. **Clone or download the project**:
   ```bash
   cd /path/to/project/directory
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment**:
   ```bash
   # macOS/Linux
   source .venv/bin/activate
   
   # Windows
   .venv\Scripts\activate
   ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## 📖 Usage

### Basic Usage

```bash
python main.py --date=2025-10-21
```

### Command Line Options

| Option | Description | Example |
|--------|-------------|---------|
| `--date` | Target date (YYYY-MM-DD) | `--date=2025-10-21` |
| `--output-format` | Output format: `structured`, `json`, `urls` | `--output-format=json` |
| `--json` | Force JSON output (shorthand) | `--json` |

### Output Formats

#### 1. Structured Data (Default)
```bash
python main.py --date=2025-10-21 --output-format=structured
```

**Output**:
```
--- Structured Data ---
Total disclosures found: 103
-----------------------
Company: イオン九州 (26530)
Time: 19:00
Title: 完全子会社の吸収合併（簡易合併）及び債権放棄に関するお知らせ
PDF: https://www.release.tdnet.info/140120251020576212.pdf
XBRL Available: No
Place: 東
ID: 623174ebb73b3a3f
```

#### 2. JSON Output
```bash
python main.py --date=2025-10-21 --json
```

**Output**:
```json
{
  "scraping_date": "2025-10-21",
  "total_disclosures": 103,
  "unique_disclosure_count": 103,
  "disclosures": [
    {
      "time": "19:00",
      "code": "26530",
      "name": "イオン九州",
      "title": "完全子会社の吸収合併（簡易合併）及び債権放棄に関するお知らせ",
      "pdf_url": "https://www.release.tdnet.info/140120251020576212.pdf",
      "xbrl_available": false,
      "place": "東",
      "history": "",
      "disclosure_date": "2025-10-21",
      "id": "623174ebb73b3a3f"
    }
  ],
  "pdf_urls": ["..."]
}
```

#### 3. URLs Only
```bash
python main.py --date=2025-10-21 --output-format=urls
```

**Output**:
```
--- PDF URLs ---
Total URLs found: 103
---------------
https://www.release.tdnet.info/140120251020576212.pdf
https://www.release.tdnet.info/140120251021576595.pdf
...
```

## 📊 Data Architecture & Modern Pydantic Models

### Pydantic V2+ Type System

The application uses modern Pydantic patterns with Annotated types for enhanced type safety:

```python
# Type Aliases with Validation
CompanyCode = Annotated[str, Field(pattern=r'^[0-9A-Z]+$', description="Company code")]
DisclosureTime = Annotated[str, Field(pattern=r'^\d{1,2}:\d{2}$', description="Time in HH:MM format")]
ExchangeCode = Annotated[str, Field(min_length=1, max_length=10, description="Stock exchange")]
```

### TdnetDisclosure Model

**Modern Pydantic V2+ model with advanced validation:**

| Field | Annotated Type | Validation | Description | Example |
|-------|---------------|------------|-------------|---------|
| `id` | `computed_field` | SHA-256 hash | Unique identifier (16 chars) | `"623174ebb73b3a3f"` |
| `time` | `DisclosureTime` | HH:MM pattern | Disclosure time | `"19:00"` |
| `code` | `CompanyCode` | Alphanumeric pattern | Company code (normalized) | `"26530"` |
| `name` | `Annotated[str, Field(min_length=1)]` | Non-empty, trimmed | Company name | `"イオン九州"` |
| `title` | `Annotated[str, Field(min_length=1)]` | Non-empty, trimmed | Document title | `"完全子会社の吸収合併..."` |
| `pdf_url` | `Annotated[HttpUrl, Field()]` | URL validation | PDF document URL | `"https://www.release..."` |
| `xbrl_available` | `Annotated[bool, Field(default=False)]` | Boolean | XBRL data availability | `true` |
| `xbrl_url` | `Annotated[Optional[HttpUrl], Field(default=None)]` | URL validation | XBRL file URL | `"https://www.release...zip"` |
| `place` | `ExchangeCode` | Length validation | Stock exchange | `"東"` |
| `history` | `Annotated[str, Field(default="")]` | Default empty | Update history | `""` |
| `disclosure_date` | `Annotated[date, Field()]` | Date validation | Disclosure date | `"2025-10-21"` |

### Advanced Validation Features

#### Field Validators
```python
@field_validator('code')
@classmethod
def validate_company_code(cls, v: str) -> str:
    """Validates and normalizes company codes (uppercase, trimmed)."""
    
@field_validator('name', 'title')  
@classmethod
def validate_non_empty_strings(cls, v: str) -> str:
    """Ensures strings are non-empty and properly trimmed."""
```

#### Model Validators
```python
@model_validator(mode='after')
def validate_xbrl_consistency(self):
    """Ensures XBRL URL is provided when XBRL is available, and vice versa."""
```

### TdnetScrapingResult Model

**Container model with comprehensive data validation:**

| Field | Type | Validation | Description |
|-------|------|------------|-------------|
| `scraping_date` | `Annotated[date, Field()]` | Date validation | Target scraping date |
| `total_disclosures` | `Annotated[int, Field(ge=0)]` | Non-negative | Count of disclosures |
| `disclosures` | `List[TdnetDisclosure]` | Model validation | List of disclosure objects |
| `pdf_urls` | `List[HttpUrl]` | URL + uniqueness | All PDF URLs found |

#### Computed Properties
```python
@computed_field
@property
def unique_disclosure_count(self) -> int:
    """Count of unique disclosure IDs."""

@computed_field  
@property
def companies_by_exchange(self) -> Dict[str, int]:
    """Statistics by stock exchange."""
```

#### Data Integrity Validation
- **Count Consistency**: `total_disclosures` must match actual disclosure count
- **URL Uniqueness**: All PDF URLs must be unique
- **Reference Integrity**: All disclosure PDF URLs must be in the main PDF URLs list

### Modern Configuration

```python
model_config = ConfigDict(
    str_strip_whitespace=True,      # Automatic whitespace trimming
    validate_assignment=True,        # Validation on field assignment
    extra='forbid',                 # Prevent unknown fields
    table_name="tdnet_disclosures", # Database integration metadata
    json_schema_extra={             # API documentation examples
        "example": { /* complete example */ }
    }
)
```

## 🔧 Advanced Usage

### Save JSON to File

```bash
python main.py --date=2025-10-21 --json > tdnet_data_20251021.json
```

### Filter by Exchange

```python
from main import scrape_tdnet_by_date
from datetime import date

result = scrape_tdnet_by_date(date(2025, 10, 21))

# Get only Tokyo Stock Exchange disclosures
tokyo_disclosures = result.get_companies_by_exchange("東")
print(f"Tokyo disclosures: {len(tokyo_disclosures)}")
```

### Find Specific Disclosure

```python
# Find by hash ID
disclosure = result.get_disclosure_by_id("623174ebb73b3a3f")
if disclosure:
    print(f"Found: {disclosure.name} - {disclosure.title}")
```

### Check for XBRL Data

```python
xbrl_disclosures = [d for d in result.disclosures if d.xbrl_available]
print(f"Disclosures with XBRL: {len(xbrl_disclosures)}")
```

## 🔍 Hash ID System

Each disclosure gets a unique 16-character hash ID based on:
- Company code
- Disclosure date  
- PDF URL (contains unique document ID)
- Time

**Benefits**:
- ✅ Guaranteed uniqueness
- ✅ Consistent across scraping runs
- ✅ Perfect for database primary keys
- ✅ Enables efficient deduplication

## 📁 Project Architecture & Structure

```
gemini-scraping-app/
├── main.py                          # 🔥 Core application (573 lines)
│   ├── Pydantic Models (Lines 29-216)
│   │   ├── TdnetDisclosure          # Modern V2+ model with validation
│   │   └── TdnetScrapingResult      # Container model with computed fields
│   ├── Utility Functions (Lines 217-382)
│   │   ├── extract_pdf_urls_from_page      # HTML parsing for PDF URLs
│   │   ├── extract_structured_data_from_page # HTML parsing for structured data
│   │   ├── has_next_page                   # Pagination detection
│   │   └── construct_tdnet_url            # URL construction
│   ├── Core Scraping (Lines 383-486)
│   │   └── scrape_tdnet_by_date           # Main scraping orchestrator
│   └── CLI Interface (Lines 487-573)
│       └── main()                         # Argument parsing and execution
├── tests/                           # 🧪 Comprehensive test suite (89% coverage)
│   ├── __init__.py                  # Package initialization
│   ├── test_main.py                 # Main test suite (29 test methods)
│   │   ├── TestTdnetDisclosureModel      # Pydantic model testing
│   │   ├── TestTdnetScrapingResult       # Container model testing  
│   │   ├── TestUtilityFunctions          # HTML parsing testing
│   │   ├── TestScrapingIntegration       # HTTP mocking and integration
│   │   ├── TestMainFunction              # CLI interface testing
│   │   └── TestErrorHandling             # Exception and edge cases
│   ├── mock_tdnet_page.html         # Realistic TDnet HTML structure
│   ├── mock_empty_page.html         # Edge case testing data
│   ├── debug_parsing.py             # Debug utilities
│   ├── test_hash_ids.py             # Hash ID demonstration  
│   └── test_hash_consistency.py     # Hash consistency verification
├── requirements.txt                 # 📦 Dependencies with exact versions
├── .gitignore                      # Git ignore patterns
├── .pytest_cache/                  # Pytest cache directory
├── __pycache__/                    # Python bytecode cache
├── HASH_ID_DOCUMENTATION.md        # Hash ID system documentation
└── README.md                       # 📚 This comprehensive documentation
```

### Code Organization Principles

#### 1. **Separation of Concerns**
- **Models**: Pure data structures with validation (Lines 29-216)
- **Parsing**: HTML processing utilities (Lines 217-382)  
- **Orchestration**: High-level scraping logic (Lines 383-486)
- **Interface**: CLI and user interaction (Lines 487-573)

#### 2. **Modern Python Patterns**
- **Type Hints**: Comprehensive typing with `Annotated` types
- **Pydantic V2+**: Advanced validation and serialization
- **Context Managers**: Proper resource management (`requests.Session`)
- **Error Handling**: Structured exception handling with logging

#### 3. **Testing Architecture**
```
tests/test_main.py Structure:
├── Setup & Fixtures (Lines 1-50)
│   ├── BaseTestCase                 # Common test setup
│   ├── Mock HTML data              # Realistic test data  
│   └── Sample disclosure data      # Pydantic model fixtures
├── Model Testing (Lines 51-120)
│   ├── Validation testing          # Field and model validators
│   ├── Hash ID generation          # Uniqueness and consistency
│   └── XBRL handling              # Complex validation logic
├── Utility Testing (Lines 121-280)
│   ├── HTML parsing accuracy       # BeautifulSoup integration
│   ├── URL construction           # TDnet URL patterns
│   └── Pagination detection       # Navigation logic
├── Integration Testing (Lines 281-350)
│   ├── HTTP mocking               # requests.Session mocking
│   ├── End-to-end workflows       # Complete scraping simulation
│   └── Error scenarios           # Network and parsing failures
└── CLI Testing (Lines 351-485)
    ├── Argument parsing           # argparse validation
    ├── Output formatting          # JSON/text output verification
    └── Error handling            # Invalid inputs and edge cases
```

## 🧪 Testing Infrastructure (89% Coverage)

### Test Coverage Breakdown
```
Name      Stmts   Miss  Cover   Missing Lines
main.py   279     34    88%     [Detailed coverage report]
Total:    279     34    88%
```

### Testing Categories

#### 1. **Unit Tests** (18 methods)
- **Pydantic Model Validation**: Field validators, model validators, computed fields
- **Utility Functions**: HTML parsing, URL construction, data extraction
- **Hash ID System**: Uniqueness, consistency, collision detection

#### 2. **Integration Tests** (7 methods)
- **HTTP Session Management**: requests.Session context manager mocking
- **End-to-End Scraping**: Complete workflow with realistic mock data
- **Error Handling**: Network failures, malformed HTML, invalid responses

#### 3. **CLI Interface Tests** (4 methods)  
- **Argument Validation**: Date formats, output options, error cases
- **Output Formatting**: JSON serialization, text formatting, error messages
- **System Integration**: sys.argv mocking, exception handling

### Mock Data Quality
- **Realistic HTML Structure**: Based on actual TDnet page analysis
- **Edge Cases**: Empty pages, malformed data, missing elements
- **XBRL Integration**: Complex scenarios with supplementary documents
- **Pagination**: Multi-page scenarios with navigation controls

### Testing Best Practices
```python
# Modern pytest patterns with fixtures
@pytest.fixture
def sample_disclosure():
    """Reusable test data fixture."""

# Comprehensive mocking with unittest.mock
@patch('main.requests.Session')
def test_integration(self, mock_session):
    """Integration test with HTTP mocking."""

# Parameterized tests for edge cases  
@pytest.mark.parametrize("input,expected", test_cases)
def test_validation(self, input, expected):
    """Data-driven validation testing."""
```

## 🗓️ Date Format Requirements

- **Format**: `YYYY-MM-DD`
- **Examples**: 
  - `2025-10-21` ✅
  - `2025-1-21` ❌ (use zero-padding)
  - `21-10-2025` ❌ (wrong order)

## ⚠️ Important Notes

1. **Rate Limiting**: The script includes reasonable delays between requests
2. **Data Availability**: TDnet may not have data for all dates (weekends, holidays)
3. **XBRL Files**: Available only for certain types of disclosures
4. **Time Zone**: All times are in Japan Standard Time (JST)

## 🛠️ Troubleshooting

### Common Issues

**"No data found for date"**
- Check if the date is a business day in Japan
- Verify the date format (YYYY-MM-DD)
- Some dates may genuinely have no disclosures

**"Connection error"**
- Check internet connection
- TDnet server may be temporarily unavailable
- Try again after a few minutes

**"Invalid date format"**
- Ensure date is in YYYY-MM-DD format
- Use zero-padding for single-digit months/days

### Debug Mode

Enable debug logging by modifying the logging level in `main.py`:

```python
logging.basicConfig(level=logging.DEBUG)
```

## 📊 Example Output Summary

For a typical business day, you might see:
- **100-150 disclosures** from major companies
- **20-30% with XBRL data** (financial reports)
- **Multiple exchanges**: 東 (Tokyo), 東名 (Tokyo/Nagoya), etc.
- **Various times**: 8:00, 15:30, 16:00, 19:00 (common disclosure times)

## � Technical Implementation Details

### HTML Parsing Strategy

#### TDnet Page Structure Analysis
```html
<table id="main-list-table">
  <tbody>
    <tr>
      <td class="kjTime">15:30</td>           <!-- Disclosure time -->
      <td class="kjCode">12345</td>           <!-- Company code -->
      <td class="kjCompany">テスト株式会社</td>    <!-- Company name -->
      <td class="kjTitle">                   <!-- Title with links -->
        <a href="*.pdf">Document Title</a>    <!-- Main PDF -->
        <a href="*.zip"><img src="xbrl.gif"></a>  <!-- XBRL data -->
      </td>
      <td class="kjPlace">東</td>             <!-- Stock exchange -->
      <td class="kjHistory">訂正</td>          <!-- Update history -->
    </tr>
  </tbody>
</table>
```

#### Parsing Logic Flow
```python
def extract_structured_data_from_page(soup, disclosure_date):
    """
    1. Find main-list-table by ID
    2. Iterate through <tr> elements  
    3. Extract data from cells with 'kj*' CSS classes
    4. Handle special cases:
       - kjTitle: Extract first PDF link as main disclosure
       - XBRL detection: Look for .zip links with XBRL images
       - Data normalization: Trim whitespace, validate formats
    5. Create TdnetDisclosure objects with validation
    """
```

### URL Construction Patterns

#### TDnet URL Format
```python
# Page 1: I_list_001_YYYYMMDD.html
# Page N: I_list_00N_YYYYMMDD.html (zero-padded to 3 digits)

BASE_URL = "https://www.release.tdnet.info"
page_url = f"{BASE_URL}/inbs/I_list_{page_num:03d}_{date_str}.html"
```

#### PDF URL Handling
```python
# PDF URLs are relative, need BASE_URL joining
pdf_url = urljoin(BASE_URL, href)  # Handles relative/absolute URLs properly
```

### Hash ID Algorithm

#### Implementation Details
```python
def generate_hash_id(self) -> str:
    """
    Creates SHA-256 hash from identifying attributes:
    - Company code (normalized)
    - Disclosure date (ISO format)  
    - PDF URL (contains unique document ID)
    - Time (handles multiple disclosures per day)
    
    Returns first 16 characters for readability while maintaining
    extremely low collision probability (1 in 18 quintillion).
    """
    unique_string = f"{self.code}_{self.disclosure_date}_{str(self.pdf_url)}_{self.time}"
    hash_object = hashlib.sha256(unique_string.encode('utf-8'))
    return hash_object.hexdigest()[:16]
```

### Request Management

#### Session Configuration
```python
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

with requests.Session() as session:
    session.headers.update(HEADERS)  # Persistent headers
    # Automatic cookie management
    # Connection pooling for efficiency
```

#### Error Handling Strategy
```python
try:
    response = session.get(page_url)
    if response.status_code == 404:
        # End of pagination reached
        break
    response.raise_for_status()  # HTTP error handling
    response.encoding = 'utf-8'  # Explicit encoding
except requests.exceptions.RequestException as e:
    # Network error handling with logging
    logging.error(f"Request failed: {e}")
```

## 🚀 Advanced Integration Examples

### Database Integration with Modern Pydantic

#### SQLAlchemy Integration
```python
from sqlalchemy import create_engine, Column, String, Boolean, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class TdnetDisclosureORM(Base):
    """SQLAlchemy model matching Pydantic structure."""
    __tablename__ = 'tdnet_disclosures'  # Matches Pydantic metadata
    
    id = Column(String(16), primary_key=True)
    time = Column(String(5))  # HH:MM format
    code = Column(String(10), index=True)  # Company code index
    name = Column(String(200))
    title = Column(String(500))
    pdf_url = Column(String(200), unique=True)
    xbrl_available = Column(Boolean, default=False)
    xbrl_url = Column(String(200), nullable=True)
    place = Column(String(10), index=True)  # Exchange index
    history = Column(String(100))
    disclosure_date = Column(Date, index=True)  # Date index for queries

# Integration function
def save_to_database(result: TdnetScrapingResult, db_url: str):
    """Save scraping result to database with Pydantic validation."""
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    with Session() as session:
        for disclosure in result.disclosures:
            # Pydantic model_dump() provides validated data
            db_record = TdnetDisclosureORM(**disclosure.model_dump(exclude={'id'}))
            db_record.id = disclosure.id  # Use computed hash ID
            session.merge(db_record)  # Upsert behavior
        session.commit()
```

#### FastAPI Integration
```python
from fastapi import FastAPI, HTTPException
from datetime import date
from typing import List

app = FastAPI(title="TDnet API", version="1.0.0")

@app.get("/disclosures/{target_date}", response_model=TdnetScrapingResult)
async def get_disclosures(target_date: date) -> TdnetScrapingResult:
    """
    Get TDnet disclosures for a specific date.
    
    - **target_date**: Date in YYYY-MM-DD format
    - **returns**: Complete scraping result with validation
    """
    try:
        result = scrape_tdnet_by_date(target_date)
        return result  # Automatic Pydantic serialization
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/disclosures/{target_date}/exchange/{exchange_code}")
async def get_disclosures_by_exchange(
    target_date: date, 
    exchange_code: str
) -> List[TdnetDisclosure]:
    """Filter disclosures by stock exchange."""
    result = scrape_tdnet_by_date(target_date)
    return result.get_companies_by_exchange(exchange_code)
```

### Data Analysis with Pandas

#### Advanced Analytics
```python
import pandas as pd
from datetime import date, timedelta

def analyze_disclosure_trends(start_date: date, end_date: date) -> pd.DataFrame:
    """Comprehensive disclosure analysis across date range."""
    all_data = []
    
    current_date = start_date
    while current_date <= end_date:
        try:
            result = scrape_tdnet_by_date(current_date)
            # Convert Pydantic models to dictionaries
            daily_data = [d.model_dump() for d in result.disclosures]
            all_data.extend(daily_data)
        except Exception as e:
            logging.warning(f"Failed to scrape {current_date}: {e}")
        current_date += timedelta(days=1)
    
    df = pd.DataFrame(all_data)
    
    # Analysis examples
    analytics = {
        'total_disclosures': len(df),
        'unique_companies': df['code'].nunique(),
        'xbrl_percentage': (df['xbrl_available'].sum() / len(df)) * 100,
        'top_exchanges': df['place'].value_counts().head(),
        'peak_hours': df['time'].value_counts().head(),
        'most_active_companies': df.groupby(['code', 'name']).size().head(10)
    }
    
    return df, analytics
```

### Machine Learning Integration

#### Feature Engineering
```python
def prepare_ml_features(disclosures: List[TdnetDisclosure]) -> pd.DataFrame:
    """Extract features for ML analysis."""
    features = []
    
    for disclosure in disclosures:
        feature_row = {
            'company_code': disclosure.code,
            'hour': int(disclosure.time.split(':')[0]),
            'exchange': disclosure.place,
            'has_xbrl': int(disclosure.xbrl_available),
            'title_length': len(disclosure.title),
            'has_correction': int('訂正' in disclosure.history),
            'is_financial_report': int('決算' in disclosure.title),
            'is_merger': int('合併' in disclosure.title),
            'disclosure_date': disclosure.disclosure_date
        }
        features.append(feature_row)
    
    return pd.DataFrame(features)
```

## 🤖 LLM Development Guide

### For AI Models Working with This Codebase

#### Code Analysis Checklist
- ✅ **Pydantic V2+ Patterns**: Modern validation with Annotated types
- ✅ **Type Safety**: Comprehensive type hints with `typing` module
- ✅ **Error Handling**: Structured exception handling with logging
- ✅ **Testing Coverage**: 89% coverage with realistic mock data
- ✅ **Documentation**: Inline docstrings and type annotations

#### Key Implementation Patterns to Understand

**1. Modern Pydantic Model Structure**
```python
class TdnetDisclosure(BaseModel):
    # Annotated types with validation
    code: CompanyCode  # Custom type alias with regex pattern
    time: DisclosureTime  # Time format validation
    
    # Field validators for data normalization
    @field_validator('code')
    @classmethod
    def validate_company_code(cls, v: str) -> str:
        return v.strip().upper()  # Normalize format
    
    # Model validators for cross-field validation  
    @model_validator(mode='after')
    def validate_xbrl_consistency(self):
        # Business logic validation
        if self.xbrl_available and self.xbrl_url is None:
            raise ValueError("XBRL URL required when available")
        return self
    
    # Computed properties
    @computed_field
    @property  
    def id(self) -> str:
        return self._generate_hash()  # Dynamic hash generation
```

**2. HTML Parsing Architecture**
```python
def extract_structured_data_from_page(soup, date):
    # Defensive parsing with fallbacks
    main_table = soup.find('table', id='main-list-table')
    if not main_table:
        return []  # Graceful degradation
    
    # CSS class-based extraction
    for cell in cells:
        if 'kjTitle' in cell.get('class', []):
            # Complex link extraction logic
            # XBRL detection with image analysis
            # Multiple PDF handling
```

**3. Session Management Pattern**  
```python
with requests.Session() as session:
    session.headers.update(HEADERS)  # Persistent configuration
    
    while True:  # Pagination loop
        response = session.get(url)  # Connection reuse
        # Error handling with specific status codes
        # Encoding management for Japanese text
```

#### Testing Strategy Understanding

**Mock Data Design**
- `mock_tdnet_page.html`: Realistic HTML structure mimicking actual TDnet pages
- Multiple disclosure types: Regular, XBRL, corrections, different exchanges
- Edge cases: Empty pages, malformed data, missing elements

**Test Categories**
```python
# Unit Tests - Isolated component testing
def test_disclosure_creation_valid(self):
    # Pydantic model validation testing
    
# Integration Tests - Component interaction  
@patch('main.requests.Session')
def test_scrape_tdnet_by_date_success(self, mock_session):
    # HTTP mocking with realistic responses
    
# CLI Tests - User interface testing
def test_main_structured_output(self):
    # sys.argv mocking and output verification
```

#### Extension Points for Development

**1. Data Storage Extensions**
```python
# Add new storage backends
def save_to_elasticsearch(result: TdnetScrapingResult):
    # Elasticsearch integration using model_dump()
    
def save_to_mongodb(result: TdnetScrapingResult):  
    # MongoDB integration with Pydantic serialization
```

**2. Analysis Extensions**
```python  
# Add computed fields to models
@computed_field
@property
def sentiment_score(self) -> float:
    # NLP analysis of disclosure titles
    
@computed_field
@property  
def market_impact_level(self) -> str:
    # Business logic for impact assessment
```

**3. API Extensions**
```python
# FastAPI endpoints using existing models
@app.get("/disclosures/{date}/analytics")
async def get_analytics(date: date) -> Dict[str, Any]:
    result = scrape_tdnet_by_date(date)
    return {
        'companies_by_exchange': result.companies_by_exchange,
        'xbrl_percentage': len(result.get_disclosures_with_xbrl()) / len(result.disclosures)
    }
```

### Development Workflow

#### 1. **Setup & Testing**
```bash
# Environment setup
python -m venv .venv
source .venv/bin/activate  
pip install -r requirements.txt

# Run tests with coverage
python -m pytest tests/ --cov=main --cov-report=html

# Manual testing
python main.py --date=2025-10-22 --json
```

#### 2. **Code Quality Checks**
```bash
# Type checking (if mypy installed)
mypy main.py

# Linting (if flake8 installed)  
flake8 main.py --max-line-length=100

# Testing specific components
python -m pytest tests/test_main.py::TestTdnetDisclosureModel -v
```

#### 3. **Debugging Strategies**
```python
# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Use debug parsing utility
from tests.debug_parsing import debug_html_structure
debug_html_structure("path/to/html/file")

# Interactive testing
from main import *
result = scrape_tdnet_by_date(date(2025, 10, 22))
result.disclosures[0].model_dump()  # Inspect data
```

## 📄 License & Usage Guidelines

### Educational & Research Use
This project is designed for:
- ✅ Educational purposes and learning modern Python/Pydantic patterns
- ✅ Research into Japanese corporate disclosure patterns  
- ✅ Data analysis and academic studies
- ✅ Building larger financial analysis systems

### Responsible Usage
- 📋 **Respect TDnet Terms**: Follow TDnet's robots.txt and terms of service
- 📋 **Rate Limiting**: Built-in delays prevent server overload
- 📋 **Data Privacy**: Public disclosure data only, no personal information
- 📋 **Attribution**: Credit original data source (TDnet) in derivative works

### Technical Support
- 📧 **Issues**: Use GitHub issues for bug reports and feature requests
- 📚 **Documentation**: Comprehensive inline documentation and type hints
- 🧪 **Testing**: 89% test coverage ensures reliability
- 🔄 **Updates**: Modern Pydantic patterns ensure future compatibility

## 🤝 Contributing Guidelines

### Code Standards
- **Type Hints**: All functions and methods must have complete type annotations
- **Pydantic Models**: Use modern V2+ patterns with Annotated types
- **Testing**: Maintain 85%+ test coverage for all new code
- **Documentation**: Comprehensive docstrings with examples

### Pull Request Process
1. **Fork & Branch**: Create feature branch from main
2. **Implement**: Follow existing patterns and conventions  
3. **Test**: Add tests for new functionality
4. **Document**: Update README and inline documentation
5. **Coverage**: Verify test coverage meets requirements

---

**Happy Scraping! 🎯**

*Built with modern Python patterns and comprehensive testing for reliable corporate disclosure data extraction.*