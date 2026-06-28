#!/bin/bash
# start.sh — One command to start the ICT Signal Dashboard
# Usage: ./start.sh

# ── Colors ────────────────────────────────────────────────
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Use printf instead of echo -e (works on all Mac shells)
print_ok()   { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
print_warn() { printf "  ${YELLOW}⚠${NC}  %s\n" "$1"; }
print_info() { printf "  ${BLUE}%s${NC}\n" "$1"; }

printf "\n"
print_info "ICT Signal Dashboard"
print_info "────────────────────"
printf "\n"

# ── Load .env file ────────────────────────────────────────
if [ -f ".env" ]; then
  export $(grep -v '^#' .env | grep -v '^$' | xargs)
  print_ok "Loaded .env file"
else
  print_warn "No .env file found. Create one with TWELVE_DATA_API_KEY=your_key"
  exit 1
fi

# ── Check API key ─────────────────────────────────────────
if [ -z "$TWELVE_DATA_API_KEY" ]; then
  print_warn "TWELVE_DATA_API_KEY is not set in .env"
  exit 1
fi
print_ok "API key found"

# ── Activate virtual environment ──────────────────────────
if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
  print_ok "Virtual environment activated"
else
  print_warn "No venv found. Run: python3.12 -m venv venv && pip install -r requirements.txt"
  exit 1
fi

# ── Start FastAPI in the background ──────────────────────
print_ok "Starting API on http://localhost:8000 ..."
uvicorn api.server:app --port 8000 --log-level warning &
API_PID=$!

# Give the server a moment to start
sleep 2

# ── Open dashboard in browser ─────────────────────────────
DASHBOARD_PATH="$(pwd)/dashboard/index.html"
print_ok "Opening dashboard..."
open "$DASHBOARD_PATH"

printf "\n"
printf "  API:       http://localhost:8000\n"
printf "  API Docs:  http://localhost:8000/docs\n"
printf "  Dashboard: file://%s\n" "$DASHBOARD_PATH"
printf "\n"
printf "  Press Ctrl+C to stop.\n"
printf "\n"

# ── Cleanly stop server on Ctrl+C ────────────────────────
cleanup() {
  printf "\n  Stopping server...\n"
  kill $API_PID 2>/dev/null
  printf "  Done.\n"
  exit 0
}
trap cleanup INT

# Keep script alive while API runs
wait $API_PID