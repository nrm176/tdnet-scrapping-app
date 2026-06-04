# TDnet Data Model

This project uses PostgreSQL through SQLAlchemy ORM models in `tdnet/orm.py`.
Source PDFs/XBRL files and parser artifacts live on disk under
`TDNET_DOWNLOAD_ROOT`; PostgreSQL stores disclosure metadata, processing state,
searchable text, tags, and analysis lineage.

## Entity Relationship Diagram

```mermaid
erDiagram
  TDNET_DISCLOSURES ||--o{ DISCLOSURE_FILES : has
  DISCLOSURE_FILES ||--o{ DOCUMENT_PARSE_JOBS : parsed_by
  DOCUMENT_PARSE_JOBS ||--o| DOCUMENT_PARSE_TEXTS : persists
  DISCLOSURE_FILES ||--o{ DOCUMENT_ANALYSIS_RESULTS : analyzed_as
  DOCUMENT_PARSE_JOBS |o--o{ DOCUMENT_ANALYSIS_RESULTS : parse_lineage
  TDNET_DISCLOSURES ||--o{ TDNET_REPORT_TAG_ASSIGNMENTS : tagged_with
  TDNET_REPORT_TAGS ||--o{ TDNET_REPORT_TAG_ASSIGNMENTS : defines
  DISCLOSURE_FILES |o--o{ TDNET_REPORT_TAG_ASSIGNMENTS : evidence_file
  DOCUMENT_PARSE_JOBS |o--o{ TDNET_REPORT_TAG_ASSIGNMENTS : evidence_parse

  TDNET_DISCLOSURES {
    string id PK
    date disclosure_date
    string time
    string code
    text name
    text title
    text pdf_url UK
    boolean xbrl_available
    text xbrl_url
    string place
    text history
    timestamptz created_at
    timestamptz updated_at
  }

  DISCLOSURE_FILES {
    int id PK
    string disclosure_id FK
    string file_type
    text source_url
    string source_file_id
    string storage_bucket
    text storage_path
    string content_type
    int file_size_bytes
    string sha256
    string download_status
    int download_attempts
    timestamptz downloaded_at
    text last_download_error
    timestamptz created_at
    timestamptz updated_at
  }

  DOCUMENT_PARSE_JOBS {
    int id PK
    int file_id FK
    string parser_name
    string parser_version
    string parse_status
    int parse_attempts
    text text_path
    string text_sha256
    timestamptz parsed_at
    text last_parse_error
    timestamptz created_at
    timestamptz updated_at
  }

  DOCUMENT_PARSE_TEXTS {
    int id PK
    int parse_job_id FK
    text content_text
    jsonb pages_json
    int page_count
    int char_count
    string content_sha256
    timestamptz created_at
    timestamptz updated_at
  }

  DOCUMENT_ANALYSIS_RESULTS {
    int id PK
    int file_id FK
    int parse_job_id FK
    string analysis_type
    string analyzer_name
    string analyzer_version
    string status
    json result_json
    text result_text
    timestamptz analyzed_at
    text last_analysis_error
    timestamptz created_at
    timestamptz updated_at
  }

  TDNET_REPORT_TAGS {
    string slug PK
    text label_ja
    text label_en
    text description
    int priority
    boolean active
    timestamptz created_at
    timestamptz updated_at
  }

  TDNET_REPORT_TAG_ASSIGNMENTS {
    int id PK
    string disclosure_id FK
    string tag_slug FK
    int file_id FK
    int parse_job_id FK
    boolean is_primary
    float confidence
    string source
    jsonb evidence_json
    string tagger_name
    string tagger_version
    timestamptz created_at
    timestamptz updated_at
  }
```

## Tables

| Table | Purpose |
| --- | --- |
| `tdnet_disclosures` | One row per scraped TDnet disclosure. The disclosure ID is the stable primary key and `pdf_url` is unique. |
| `disclosure_files` | One row per source artifact type for a disclosure, currently PDF or XBRL. Tracks source URL, expected local path, hash, size, download status, attempts, and errors. |
| `document_parse_jobs` | One row per file and parser identity. Tracks parser name/version, status, attempts, text artifact path, hash, parse time, and errors. |
| `document_parse_texts` | Searchable text cache for completed parse jobs. Stores normalized full text plus page-level JSON used by search/detail views. |
| `document_analysis_results` | Reserved downstream analysis outputs. Keeps analyzer lineage separate from download and parse state. |
| `tdnet_report_tags` | Deterministic report tag taxonomy. |
| `tdnet_report_tag_assignments` | Disclosure-level tag assignments with optional file/parse-job evidence lineage. |

## Key Constraints

- `tdnet_disclosures.pdf_url` is unique.
- `disclosure_files` is unique on `(disclosure_id, file_type)`, so each disclosure has at most one row per source artifact type.
- `document_parse_jobs` is unique on `(file_id, parser_name, parser_version)`, so reruns update or skip the same parser identity instead of creating duplicates.
- `document_parse_texts.parse_job_id` is unique, giving each parse job at most one searchable text cache row.
- `tdnet_report_tag_assignments` is unique on `(disclosure_id, tag_slug)`, so each tag appears at most once per disclosure.

## Processing Lineage

The main ingestion lineage is:

```text
tdnet_disclosures
  -> disclosure_files
  -> document_parse_jobs
  -> document_parse_texts
```

Downstream analysis and tagging keep their own lineage:

```text
document_analysis_results -> disclosure_files, optional document_parse_jobs
tdnet_report_tag_assignments -> tdnet_disclosures, tdnet_report_tags, optional disclosure_files, optional document_parse_jobs
```

This separation lets parser history, searchable text, tagging, and later
business analysis evolve independently.

## Parser Identities

Parser identity is `(parser_name, parser_version)`. Current parser names are:

| Parser name | Source | Command |
| --- | --- | --- |
| `pymupdf4llm` | PDF | `tdnet parse` |
| `apple-vision-ocr` | PDF rendered to page images | `tdnet ocr` |
| `tdnet-ixbrl-text` | downloaded XBRL/iXBRL ZIP sidecar | `tdnet parse-ixbrl` |

The review app lists completed parser options by parser name plus parser
version through `/api/parsers`.

## Disk Artifacts

Large source and parser artifacts are intentionally not stored in PostgreSQL.
`disclosure_files.storage_path` points to downloaded PDFs/XBRL files under
`TDNET_DOWNLOAD_ROOT`, and `document_parse_jobs.text_path` points to the durable
markdown artifact under the disclosure's `parsed/` directory.

Examples:

```text
/Volumes/yakushimachi/Downloads/tdnet/140120260515538453/
  140120260515538453.pdf
  081220260515538453.zip
  parsed/
    pymupdf4llm.<version>.md
    pymupdf4llm.<version>.pages.json
    pymupdf4llm.<version>.meta.json
```

`document_parse_texts` stores a searchable cache of those parsed artifacts, not
the source-of-truth files themselves.
