#!/usr/bin/env bash
# ============================================================
# Green Workload AI — Start all services
# Starts the AI agent scheduler and the web dashboard
# ============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT_DIR/.pids"
LOG_DIR="$ROOT_DIR/.logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# ── Colours ────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✔]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✖]${NC} $*"; }

# ── Check if already running ──────────────────────────────────
is_running() {
  local pidfile="$1"
  if [ -f "$pidfile" ]; then
    local pid
    pid=$(<"$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    rm -f "$pidfile"
  fi
  return 1
}

# ── Load .env ─────────────────────────────────────────────────
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

DASHBOARD_PORT="${DASHBOARD_PORT:-3099}"

echo ""
echo "🌿 Green Workload AI — Starting Services"
echo "========================================="

# ── 1. AI Agent Scheduler ─────────────────────────────────────
if is_running "$PID_DIR/agent.pid"; then
  warn "AI Agent already running (PID $(<"$PID_DIR/agent.pid"))"
else
  echo -n "   Starting AI Agent scheduler... "
  cd "$ROOT_DIR"
  nohup python main.py \
    > "$LOG_DIR/agent.log" 2>&1 &
  echo $! > "$PID_DIR/agent.pid"
  info "AI Agent started (PID $!) — logs: .logs/agent.log"
fi

# ── 2. Web Dashboard ─────────────────────────────────────────
if is_running "$PID_DIR/dashboard.pid"; then
  warn "Dashboard already running (PID $(<"$PID_DIR/dashboard.pid"))"
else
  echo -n "   Starting Dashboard server... "
  cd "$ROOT_DIR/web-dashboard"
  nohup node server.js \
    > "$LOG_DIR/dashboard.log" 2>&1 &
  echo $! > "$PID_DIR/dashboard.pid"
  info "Dashboard started (PID $!) — logs: .logs/dashboard.log"
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "   Services:"
echo "   ─────────────────────────────────────────"
printf "   AI Agent     PID %-8s %s\n" "$(<"$PID_DIR/agent.pid")" "python main.py"
printf "   Dashboard    PID %-8s %s\n" "$(<"$PID_DIR/dashboard.pid")" "http://localhost:$DASHBOARD_PORT"
echo "   ─────────────────────────────────────────"
echo ""
echo "   Stop all:  ./stop.sh"
echo "   View logs: tail -f .logs/agent.log .logs/dashboard.log"
echo ""
