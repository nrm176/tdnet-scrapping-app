#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" != "--legacy-local" ]]; then
  printf '%s\n' 'This legacy OCR pipeline requires --legacy-local as its first argument.' >&2
  exit 2
fi
shift

exec "${SCRIPT_DIR}/tdnet_all_in_one.sh" --legacy-local --with-ocr "$@"
