#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  kill "${BACKEND_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd backend
# --reload: this is the dev workflow, and without it a `git pull` hot-reloads
# the Vite frontend while the API keeps serving the old code — the new UI then
# calls endpoints the running server doesn't have and gets 405s back.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app &
BACKEND_PID=$!
cd ../frontend
npm run dev -- --host 0.0.0.0 --port 5000