#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${SCRIPT_DIR}/frontend"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
REQUESTED_BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
REQUESTED_FRONTEND_PORT="${FRONTEND_PORT:-5173}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    kill "${FRONTEND_PID}" 2>/dev/null || true
  fi
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
  wait "${FRONTEND_PID:-}" "${BACKEND_PID:-}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

port_in_use() {
  local port="$1"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

find_available_port() {
  local port="$1"
  local max_port=$((port + 100))
  while port_in_use "${port}"; do
    port=$((port + 1))
    if ((port > max_port)); then
      echo "Could not find an available port after ${max_port}." >&2
      exit 1
    fi
  done
  echo "${port}"
}

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to run the backend." >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to run the frontend." >&2
  exit 1
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "Installing frontend dependencies..."
  (cd "${FRONTEND_DIR}" && npm install)
fi

BACKEND_PORT="$(find_available_port "${REQUESTED_BACKEND_PORT}")"
FRONTEND_PORT="$(find_available_port "${REQUESTED_FRONTEND_PORT}")"

if [[ "${BACKEND_PORT}" != "${REQUESTED_BACKEND_PORT}" ]]; then
  echo "Backend port ${REQUESTED_BACKEND_PORT} is in use; using ${BACKEND_PORT}."
fi
if [[ "${FRONTEND_PORT}" != "${REQUESTED_FRONTEND_PORT}" ]]; then
  echo "Frontend port ${REQUESTED_FRONTEND_PORT} is in use; using ${FRONTEND_PORT}."
fi

echo "Starting TDnet review backend on http://${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "${REPO_ROOT}"
  uv run --extra layout uvicorn app.backend.main:app \
    --host "${BACKEND_HOST}" \
    --port "${BACKEND_PORT}"
) &
BACKEND_PID="$!"

echo "Starting TDnet review frontend on http://${FRONTEND_HOST}:${FRONTEND_PORT}"
(
  cd "${FRONTEND_DIR}"
  VITE_BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}" \
    npm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}" --strictPort
) &
FRONTEND_PID="$!"

echo
echo "TDnet review app is starting:"
echo "  Frontend: http://${FRONTEND_HOST}:${FRONTEND_PORT}"
echo "  Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}/api/health"
echo
echo "Press Ctrl-C to stop both processes."

while true; do
  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    echo "Backend process exited." >&2
    exit 1
  fi
  if ! kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    echo "Frontend process exited." >&2
    exit 1
  fi
  sleep 1
done
