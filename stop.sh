#!/usr/bin/env bash
# =============================================================================
#  Apex-Omega-v6  —  Graceful Stop
#  Usage:  ./stop.sh
#  Sends SIGTERM to all processes tracked by start.sh.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${REPO_ROOT}/.apex_pids"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[APEX]${NC} $*"; }
success() { echo -e "${GREEN}[APEX]${NC} $*"; }
error()   { echo -e "${RED}[APEX]${NC} $*" >&2; }

if [ ! -f "${PID_FILE}" ]; then
  error "No .apex_pids file found — is the system running?"
  exit 1
fi

info "Stopping Apex-Omega-v6 services …"
STOPPED=0
while IFS='=' read -r label pid; do
  [[ -z "${pid}" ]] && continue
  if kill -0 "${pid}" 2>/dev/null; then
    info "  Stopping ${label} (PID ${pid})"
    kill -TERM "${pid}" 2>/dev/null || true
    STOPPED=$((STOPPED + 1))
  else
    info "  ${label} (PID ${pid}) already stopped."
  fi
done < "${PID_FILE}"

rm -f "${PID_FILE}"
success "Done — stopped ${STOPPED} process(es)."
