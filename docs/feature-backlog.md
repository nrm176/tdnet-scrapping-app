# TDnet Feature Backlog

This backlog captures the next product and engineering improvements for the
canonical TDnet PostgreSQL application. The theme is to move from a working
ingestion and review system toward a disclosure intelligence workbench with
observable pipeline health, durable review decisions, and structured business
facts.

Issues #1 and #2 landed through PR #9.

Issue #3 is being implemented in the
`codex/company-disclosure-timeline` branch.

## Priority Picks

| Priority | Feature | GitHub issue | Why it matters |
| --- | --- | --- | --- |
| 1 | Make iXBRL fallback first-class | [#1](https://github.com/nrm176/tdnet-scrapping-app/issues/1) | The project now has three parser identities, but `tdnet parse-ixbrl` is still separate from the all-in-one flow. |
| 2 | Parser quality dashboard | [#2](https://github.com/nrm176/tdnet-scrapping-app/issues/2) | Operators need a compact way to see parser coverage, failures, sparse text, OCR candidates, and iXBRL fallback opportunities. |
| 3 | Company disclosure timeline | [#3](https://github.com/nrm176/tdnet-scrapping-app/issues/3) | A company-centric view would make repeated disclosures, corrections, earnings releases, and guidance changes easier to inspect. |
| 4 | Structured financial extraction | [#4](https://github.com/nrm176/tdnet-scrapping-app/issues/4) | `document_analysis_results` is ready for extracted financial facts and business metrics with parser lineage. |
| 5 | Better search experience | [#5](https://github.com/nrm176/tdnet-scrapping-app/issues/5) | Search can become more useful with highlighted snippets, page jumps, parser preferences, saved filters, and richer controls. |
| 6 | Pipeline run history | [#6](https://github.com/nrm176/tdnet-scrapping-app/issues/6) | Logs are useful, but persisted run/step history would make ingestion health visible in the app and API. |
| 7 | Manual review workflow | [#7](https://github.com/nrm176/tdnet-scrapping-app/issues/7) | Parser QA decisions should be durable instead of living only in generated review reports. |
| 8 | Read-only API expansion | [#8](https://github.com/nrm176/tdnet-scrapping-app/issues/8) | The API can expose richer parser status, company timelines, tag summaries, and artifact lineage while preserving read-only semantics. |

## Feature Details

### 1. Make iXBRL Fallback First-Class

Add a `--with-ixbrl` option to `scripts/tdnet_all_in_one.sh` and run
`tdnet parse-ixbrl` after the main PyMuPDF parse. Keep the command opt-in at
first, with explicit limits, retry behavior, and log summaries matching the
existing parse/OCR phases.

### 2. Parser Quality Dashboard

Add an operational view that summarizes parser health by parser name/version:
completed jobs, searchable text rows, failures, sparse or garbled candidates,
OCR candidates, iXBRL candidates, throughput, and recent errors.

### 3. Company Disclosure Timeline

Add a company-code detail view showing disclosures over time, tags, parser
status, search snippets, and artifact lineage. This would help review repeated
forecast corrections, earnings releases, governance reports, and later business
analysis outputs.

### 4. Structured Financial Extraction

Use `document_analysis_results` to store extracted facts such as forecast
revision values, net sales, operating profit, ordinary profit, net income, EPS,
dividend changes, and guidance deltas. Keep analyzer name/version and parser
lineage explicit.

### 5. Better Search Experience

Improve the review workbench with highlighted snippets, page jump links, parser
preference labels, saved filters, tag filter ergonomics, and a toggle for best
parse only versus all parser outputs.

### 6. Pipeline Run History

Persist pipeline runs and step metrics in PostgreSQL: run ID, date range,
checkpoint decision, options, step start/finish, elapsed time, counts,
throughput, failures, and log path. Expose this through the review UI.

### 7. Manual Review Workflow

Add durable review states such as `needs_review`, `accepted`, `bad_parse`,
`prefer_ocr`, and `prefer_ixbrl`, plus reviewer notes. This would turn parser QA
from a generated HTML report into a searchable workflow.

### 8. Read-Only API Expansion

Add read-only endpoints for parser status by disclosure, company timelines,
tag summaries, parse quality summaries, and artifact lineage. Keep the existing
read-only API contract unless a future change explicitly asks to add writes.
