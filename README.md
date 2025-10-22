# TDnet Scraping Application

A robust Python application for scraping Japanese corporate disclosure data from TDnet (Timely Disclosure Network). The application extracts structured data from disclosure documents and provides unique hash IDs for database integration.

## 🚀 Features

- **Date-based Scraping**: Extract disclosures for any specific date
- **Structured Data**: Clean, validated data using Pydantic models
- **XBRL Support**: Handles XBRL files when available
- **Unique Hash IDs**: SHA-256 based unique identifiers for each record
- **Multiple Output Formats**: JSON, structured data, or PDF URLs only
- **Pagination Support**: Automatically handles multiple pages
- **Data Validation**: Robust error handling and data integrity checks

## 📋 Requirements

- Python 3.8+
- Virtual environment (recommended)

## 🛠️ Installation

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

## 📊 Data Structure

### TdnetDisclosure Model

Each disclosure record contains:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `id` | `str` | Unique hash ID (16 chars) | `"623174ebb73b3a3f"` |
| `time` | `str` | Disclosure time | `"19:00"` |
| `code` | `str` | Company code | `"26530"` |
| `name` | `str` | Company name | `"イオン九州"` |
| `title` | `str` | Document title | `"完全子会社の吸収合併..."` |
| `pdf_url` | `HttpUrl` | PDF document URL | `"https://www.release..."` |
| `xbrl_available` | `bool` | XBRL data available | `true` |
| `xbrl_url` | `HttpUrl` | XBRL file URL (if available) | `"https://www.release...zip"` |
| `place` | `str` | Stock exchange | `"東"` |
| `history` | `str` | Update history | `""` |
| `disclosure_date` | `date` | Date of disclosure | `"2025-10-21"` |

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

## 📁 Project Structure

```
gemini-scraping-app/
├── main.py                     # Main application
├── requirements.txt            # Dependencies
├── .gitignore                 # Git ignore rules
├── .venv/                     # Virtual environment
├── test_hash_ids.py           # Hash ID demonstration
├── test_hash_consistency.py   # Consistency tests
├── HASH_ID_DOCUMENTATION.md   # Hash ID details
└── README.md                  # This file
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

## 🚀 Integration Examples

### Database Integration

```python
# Example for SQLAlchemy
from sqlalchemy import create_engine, Column, String, Boolean, Date
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class TdnetDisclosure(Base):
    __tablename__ = 'tdnet_disclosures'
    
    id = Column(String(16), primary_key=True)
    code = Column(String(10))
    name = Column(String(200))
    # ... other fields
```

### Data Analysis

```python
import pandas as pd

# Convert to DataFrame
data = result.model_dump()
df = pd.DataFrame(data['disclosures'])

# Analysis examples
print(f"Most active company: {df['name'].value_counts().head(1)}")
print(f"XBRL availability: {df['xbrl_available'].sum()} out of {len(df)}")
```

## 📄 License

This project is for educational and research purposes. Please respect TDnet's terms of service and use responsibly.

## 🤝 Contributing

Feel free to submit issues, feature requests, or pull requests to improve the application.

---

**Happy Scraping! 🎯**