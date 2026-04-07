#!/usr/bin/env bash
# ============================================================
# Green Workload AI — Stop all services
# Gracefully stops the AI agent scheduler and web dashboard
# ============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT_DIR/.pids"

# ── Colours ────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✔]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✖]${NC} $*"; }

stop_service() {
  local name="$1"
  local pidfile="$PID_DIR/$2.pid"

  if [ ! -f "$pidfile" ]; then
    warn "$name — no PID file found (not running?)"
    return 0
  fi

  local pid
  pid=$(<"$pidfile")

  if ! kill -0 "$pid" 2>/dev/null; then
    warn "$name — process $pid already exited"
    rm -f "$pidfile"
    return 0
  fi

  echo -n "   Stopping $name (PID $pid)... "

  # Graceful shutdown (SIGTERM), wait up to 10s
  kill "$pid" 2>/dev/null || true
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [ $waited -lt 10 ]; do
    sleep 1
    waited=$((waited + 1))
  done

  # Force kill if still alive
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    warn "$name — force killed"
  else
    info "$name stopped"
  fi

  rm -f "$pidfile"
}

echo ""
echo "🌿 Green Workload AI — Stopping Services"
echo "========================================="

stop_service "AI Agent"  "agent"
stop_service "Dashboard" "dashboard"

echo ""
info "All services stopped"
echo ""
