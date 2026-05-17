#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DAYS="30"
START_DATE=""
END_DATE=""
DOWNLOAD_LIMIT="20000"
DOWNLOAD_CONCURRENCY="8"
PARSE_LIMIT="20000"
PARSE_WORKERS=""
PARSE_TEXT_LIMIT="20000"
WITH_OCR="false"
OCR_LIMIT="100"
OCR_WORKERS="4"
WITH_REVIEW="false"
REVIEW_LIMIT="50"
REVIEW_STRATEGY="suspicious"
RETRY_FAILED="false"
SKIP_INSTALL="false"
SKIP_POSTGRES="false"
SKIP_SCRAPE="false"
SKIP_DOWNLOAD="false"
SKIP_PARSE="false"
SKIP_PARSE_TEXT="false"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Run the TDnet pipeline end to end.

Default pipeline:
  1. Start Docker Postgres
  2. Ensure local Python environment
  3. Scrape/persist the last 30 days
  4. Download missing PDF/XBRL artifacts
  5. Parse downloaded PDFs
  6. Backfill searchable parse text

Usage:
  scripts/tdnet_all_in_one.sh [options]

Date options:
  --days N                 Number of days through --end-date. Default: 30
  --start-date YYYY-MM-DD  Explicit start date. Overrides --days
  --end-date YYYY-MM-DD    End date. Default: today in Asia/Tokyo

Pipeline options:
  --download-limit N       Download candidate disclosure limit. Default: 20000
  --download-concurrency N Async download concurrency. Default: 8
  --parse-limit N          Parse candidate file limit. Default: 20000
  --parse-workers N        Parser worker processes. Default: tdnet CLI default
  --parse-text-limit N     Parse text backfill limit. Default: 20000
  --retry-failed           Retry failed download/parse/parse-text/OCR rows
  --with-ocr               Also run Apple Vision OCR for sparse parses
  --ocr-limit N            OCR candidate limit. Default: 100
  --ocr-workers N          OCR workers. Default: 4
  --with-review            Generate a parse review report
  --review-limit N         Review report document limit. Default: 50
  --review-strategy NAME   suspicious|random|recent|forecast-correction. Default: suspicious

Skip options:
  --skip-install
  --skip-postgres
  --skip-scrape
  --skip-download
  --skip-parse
  --skip-parse-text

Other:
  --dry-run                Print commands without executing them
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) DAYS="$2"; shift 2 ;;
    --start-date) START_DATE="$2"; shift 2 ;;
    --end-date) END_DATE="$2"; shift 2 ;;
    --download-limit) DOWNLOAD_LIMIT="$2"; shift 2 ;;
    --download-concurrency) DOWNLOAD_CONCURRENCY="$2"; shift 2 ;;
    --parse-limit) PARSE_LIMIT="$2"; shift 2 ;;
    --parse-workers) PARSE_WORKERS="$2"; shift 2 ;;
    --parse-text-limit) PARSE_TEXT_LIMIT="$2"; shift 2 ;;
    --retry-failed) RETRY_FAILED="true"; shift ;;
    --with-ocr) WITH_OCR="true"; shift ;;
    --ocr-limit) OCR_LIMIT="$2"; shift 2 ;;
    --ocr-workers) OCR_WORKERS="$2"; shift 2 ;;
    --with-review) WITH_REVIEW="true"; shift ;;
    --review-limit) REVIEW_LIMIT="$2"; shift 2 ;;
    --review-strategy) REVIEW_STRATEGY="$2"; shift 2 ;;
    --skip-install) SKIP_INSTALL="true"; shift ;;
    --skip-postgres) SKIP_POSTGRES="true"; shift ;;
    --skip-scrape) SKIP_SCRAPE="true"; shift ;;
    --skip-download) SKIP_DOWNLOAD="true"; shift ;;
    --skip-parse) SKIP_PARSE="true"; shift ;;
    --skip-parse-text) SKIP_PARSE_TEXT="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

cd "${REPO_ROOT}"
mkdir -p logs
RUN_ID="$(date '+%Y%m%d-%H%M%S')"
PIPELINE_LOG="logs/tdnet-all-in-one-${RUN_ID}.log"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "${PIPELINE_LOG}"
}

run() {
  log "+ $*"
  if [[ "${DRY_RUN}" == "true" ]]; then
    return 0
  fi
  "$@" 2>&1 | tee -a "${PIPELINE_LOG}"
}

find_python() {
  if command -v python3.13 >/dev/null 2>&1; then
    echo "python3.13"
  elif command -v python3.12 >/dev/null 2>&1; then
    echo "python3.12"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  else
    echo "python"
  fi
}

wait_for_postgres() {
  local attempts=40
  local i
  for ((i = 1; i <= attempts; i++)); do
    if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-tdnet}" -d "${POSTGRES_DB:-tdnet}" >/dev/null 2>&1; then
      log "Postgres is ready."
      return 0
    fi
    sleep 1
  done
  echo "Postgres did not become ready after ${attempts}s." >&2
  return 1
}

SYSTEM_PYTHON="$(find_python)"

if [[ "${SKIP_POSTGRES}" != "true" ]]; then
  run docker compose up -d postgres
  if [[ "${DRY_RUN}" != "true" ]]; then
    wait_for_postgres
  fi
fi

if [[ ! -x ".venv/bin/python" || "${SKIP_INSTALL}" != "true" ]]; then
  if [[ ! -x ".venv/bin/python" ]]; then
    run "${SYSTEM_PYTHON}" -m venv .venv
  fi
fi

PYTHON=".venv/bin/python"
TDNET=".venv/bin/tdnet"

if [[ "${SKIP_INSTALL}" != "true" ]]; then
  run "${PYTHON}" -m pip install --upgrade pip
  run "${PYTHON}" -m pip install -e ".[dev]"
fi

if [[ ! -x "${TDNET}" ]]; then
  echo "tdnet CLI was not found at ${TDNET}. Run without --skip-install first." >&2
  exit 1
fi

DATE_JSON="$("${PYTHON}" - "$DAYS" "$START_DATE" "$END_DATE" <<'PY'
import json
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

days = int(sys.argv[1])
start_arg = sys.argv[2] or None
end_arg = sys.argv[3] or None

end = datetime.strptime(end_arg, "%Y-%m-%d").date() if end_arg else datetime.now(ZoneInfo("Asia/Tokyo")).date()
start = datetime.strptime(start_arg, "%Y-%m-%d").date() if start_arg else end - timedelta(days=days - 1)
if start > end:
    raise SystemExit("start date must be before or equal to end date")

dates = []
current = start
while current <= end:
    dates.append(current.isoformat())
    current += timedelta(days=1)

print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "dates": dates}))
PY
)"

START_DATE="$("${PYTHON}" -c 'import json,sys; print(json.loads(sys.argv[1])["start"])' "${DATE_JSON}")"
END_DATE="$("${PYTHON}" -c 'import json,sys; print(json.loads(sys.argv[1])["end"])' "${DATE_JSON}")"

mapfile -t DATES < <("${PYTHON}" -c 'import json,sys; print("\n".join(json.loads(sys.argv[1])["dates"]))' "${DATE_JSON}")

log "TDnet all-in-one pipeline started run_id=${RUN_ID}"
log "Date range: ${START_DATE} through ${END_DATE} (${#DATES[@]} days)"
log "Pipeline log: ${PIPELINE_LOG}"

if [[ "${SKIP_SCRAPE}" != "true" ]]; then
  for scrape_date in "${DATES[@]}"; do
    log "Scraping and persisting ${scrape_date}"
    if [[ "${DRY_RUN}" == "true" ]]; then
      log "+ ${TDNET} scrape --date ${scrape_date} --persist"
    else
      "${TDNET}" scrape --date "${scrape_date}" --persist --output-format urls >/tmp/tdnet-all-in-one-scrape.out 2>&1
      tail -n 20 /tmp/tdnet-all-in-one-scrape.out | tee -a "${PIPELINE_LOG}"
    fi
  done
fi

if [[ "${SKIP_DOWNLOAD}" != "true" ]]; then
  DOWNLOAD_ARGS=(download --limit "${DOWNLOAD_LIMIT}" --concurrency "${DOWNLOAD_CONCURRENCY}")
  if [[ "${RETRY_FAILED}" == "true" ]]; then
    DOWNLOAD_ARGS+=(--retry-failed)
  fi
  run "${TDNET}" "${DOWNLOAD_ARGS[@]}"
fi

if [[ "${SKIP_PARSE}" != "true" ]]; then
  PARSE_ARGS=(parse --limit "${PARSE_LIMIT}")
  if [[ -n "${PARSE_WORKERS}" ]]; then
    PARSE_ARGS+=(--workers "${PARSE_WORKERS}")
  fi
  if [[ "${RETRY_FAILED}" == "true" ]]; then
    PARSE_ARGS+=(--retry-failed)
  fi
  run "${TDNET}" "${PARSE_ARGS[@]}"
fi

if [[ "${SKIP_PARSE_TEXT}" != "true" ]]; then
  run "${TDNET}" persist-parse-text --limit "${PARSE_TEXT_LIMIT}"
fi

if [[ "${WITH_OCR}" == "true" ]]; then
  OCR_ARGS=(ocr --strategy low-text --limit "${OCR_LIMIT}" --workers "${OCR_WORKERS}")
  if [[ "${RETRY_FAILED}" == "true" ]]; then
    OCR_ARGS+=(--retry-failed)
  fi
  run "${TDNET}" "${OCR_ARGS[@]}"
fi

if [[ "${WITH_REVIEW}" == "true" ]]; then
  run "${TDNET}" review-parse --strategy "${REVIEW_STRATEGY}" --limit "${REVIEW_LIMIT}"
fi

log "TDnet all-in-one pipeline finished run_id=${RUN_ID}"
