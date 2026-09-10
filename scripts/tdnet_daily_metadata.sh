#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESEARCH_ROOT="${RESEARCH_ROOT:-/Users/nrm176p/GitHub2/hedge-fund-takedo}"
RESEARCH_PYTHON="${RESEARCH_PYTHON:-${RESEARCH_ROOT}/.venv/bin/python}"
TDNET_PYTHON="${TDNET_PYTHON:-/Users/nrm176p/GitHub2/tdnet-api/tdnet-scraping-app/.venv/bin/python}"
DATE=""
OUTPUT=""
DATE_SEEN="false"
OUTPUT_SEEN="false"

usage() {
  printf '%s\n' "Usage: scripts/tdnet_daily_metadata.sh --date YYYY-MM-DD [--output PATH]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      [[ "${DATE_SEEN}" == "false" ]] || { printf '%s\n' 'Duplicate --date is not allowed.' >&2; exit 2; }
      [[ $# -ge 2 ]] || { usage; exit 2; }
      DATE="$2"
      DATE_SEEN="true"
      shift 2
      ;;
    --output)
      [[ "${OUTPUT_SEEN}" == "false" ]] || { printf '%s\n' 'Duplicate --output is not allowed.' >&2; exit 2; }
      [[ $# -ge 2 ]] || { usage; exit 2; }
      OUTPUT="$2"
      OUTPUT_SEEN="true"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ ! "${DATE}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  printf '%s\n' 'Expected exactly one valid --date YYYY-MM-DD.' >&2
  exit 2
fi
if ! DATE="${DATE}" python3 -c 'import os; from datetime import date; date.fromisoformat(os.environ["DATE"])' >/dev/null 2>&1; then
  printf 'Invalid date: %s\n' "${DATE}" >&2
  exit 2
fi
if [[ ! -x "${RESEARCH_PYTHON}" ]]; then
  printf '%s\n' 'Research controller Python dependency is missing.' >&2
  exit 127
fi
if [[ ! -x "${TDNET_PYTHON}" ]]; then
  printf '%s\n' 'TDnet Python dependency is missing.' >&2
  exit 127
fi

ARGS=("${TDNET_PYTHON}" -m tdnet.research_ingest --date "${DATE}")
if [[ -n "${OUTPUT}" ]]; then
  ARGS+=(--output "${OUTPUT}")
fi

cd "${REPO_ROOT}"
export PYTHONPATH="${RESEARCH_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "${RESEARCH_PYTHON}" -m hedge_research.disclosure_environment exec tdnet -- "${ARGS[@]}"
