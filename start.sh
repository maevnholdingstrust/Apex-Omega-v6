#!/usr/bin/env bash
# =============================================================================
#  Apex-Omega-v6  —  One-Click Boot
#  Usage:  ./start.sh [--dry-run] [--dashboard-only] [--bot-only]
#
#  Flags
#    --dry-run        Force LIVE_EXECUTION=false and APEX_SEND_TX=0
#                     (safe shadow-mode; default when LIVE_EXECUTION is unset)
#    --dashboard-only Start the Flask dashboard only (no bot process)
#    --bot-only       Start the arbitrage bot only (no dashboard server)
#    --no-build       Skip Rust wheel build (use cached wheel if present)
#    --help           Show this message and exit
#
#  Environment
#    All configuration is read from python/apex_omega_core/.env (or a .env
#    file in the repo root) plus any variables already present in the shell.
#    Shell variables take precedence over the .env file.
#    Sensitive values (PRIVATE_KEY, RPC URLs, API keys) must be supplied via
#    that file or the shell — they are never hardcoded here.
#
#  Logs
#    dashboard.log   — Flask / gunicorn output
#    bot.log         — Arbitrage bot output
#    PIDs written to .apex_pids so stop.sh can find them.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${REPO_ROOT}/.apex_pids"
DASHBOARD_LOG="${REPO_ROOT}/dashboard.log"
BOT_LOG="${REPO_ROOT}/bot.log"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[APEX]${NC} $*"; }
success() { echo -e "${GREEN}[APEX]${NC} $*"; }
warn()    { echo -e "${YELLOW}[APEX]${NC} $*"; }
error()   { echo -e "${RED}[APEX]${NC} $*" >&2; }
header()  { echo -e "\n${BOLD}${BLUE}══════════════════════════════════════════${NC}"; \
            echo -e "${BOLD}${BLUE}  $*${NC}"; \
            echo -e "${BOLD}${BLUE}══════════════════════════════════════════${NC}"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
DRY_RUN=false
DASHBOARD_ONLY=false
BOT_ONLY=false
NO_BUILD=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)        DRY_RUN=true ;;
    --dashboard-only) DASHBOARD_ONLY=true ;;
    --bot-only)       BOT_ONLY=true ;;
    --no-build)       NO_BUILD=true ;;
    --help|-h)
      sed -n '2,21p' "$0" | sed 's/^#[[:space:]]*//'
      exit 0 ;;
    *) error "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
header "Apex-Omega-v6  /  Full System Boot"
info "Timestamp : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
info "Repo root : ${REPO_ROOT}"

# ── Load .env (shell vars take precedence) ────────────────────────────────────
# Prefer the canonical .env inside the python package; fall back to repo root.
ENV_FILE="${REPO_ROOT}/python/apex_omega_core/.env"
if [ ! -f "${ENV_FILE}" ]; then
  ENV_FILE="${REPO_ROOT}/.env"
fi

if [ -f "${ENV_FILE}" ]; then
  info "Loading environment from ${ENV_FILE}"
  # Export each non-comment, non-empty line while respecting existing shell values.
  # Only keys matching the safe pattern [A-Za-z_][A-Za-z0-9_]* are processed to
  # prevent shell injection via crafted .env files.
  while IFS= read -r line || [ -n "$line" ]; do
    # Skip blanks and comments
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    # Strip inline comments
    line="${line%%#*}"
    line="${line%"${line##*[![:space:]]}"}"  # rtrim
    [[ -z "$line" ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    # Validate key: must be a safe identifier (no spaces, special chars)
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      warn "Skipping malformed .env key: '${key}'"
      continue
    fi
    # Only export if not already set in the shell environment
    if [ -z "${!key+x}" ]; then
      export "$key"="$value"
    fi
  done < "${ENV_FILE}"
else
  warn ".env file not found — relying on shell environment variables only."
fi

# ── Safety: default LIVE_EXECUTION to false ───────────────────────────────────
if $DRY_RUN; then
  export LIVE_EXECUTION=false
  export ARM_LIVE_EXECUTION=false
  export APEX_SEND_TX=0
  warn "DRY-RUN mode: LIVE_EXECUTION=false, APEX_SEND_TX=0"
else
  # If not explicitly armed, default to shadow/dry-run.
  : "${LIVE_EXECUTION:=false}"
  : "${ARM_LIVE_EXECUTION:=false}"
  : "${APEX_SEND_TX:=0}"
  export LIVE_EXECUTION ARM_LIVE_EXECUTION APEX_SEND_TX
  if [ "${LIVE_EXECUTION}" != "true" ]; then
    info "Shadow mode active (LIVE_EXECUTION=${LIVE_EXECUTION})"
  else
    warn "LIVE EXECUTION IS ENABLED — real transactions may be submitted."
  fi
fi

# ── RPC endpoint (polygon.drpc.org is in the repo's allowed domain list) ──────
RPC_URL="${POLYGON_RPC:-${POLYGON_HTTP:-${APEX_RPC_URL:-}}}"
if [ -z "${RPC_URL}" ]; then
  warn "No RPC URL found in environment — falling back to public polygon.drpc.org"
  warn "Set POLYGON_RPC in your .env for production use."
  RPC_URL="https://polygon.drpc.org"
fi
export POLYGON_RPC="${RPC_URL}"
export APEX_RPC_URL="${RPC_URL}"
info "RPC endpoint : ${RPC_URL}"

# ── Python interpreter ────────────────────────────────────────────────────────
PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" &>/dev/null; then
  PYTHON="python"
fi
if ! command -v "${PYTHON}" &>/dev/null; then
  error "Python not found. Install Python 3.8+ and try again."
  exit 1
fi
PYTHON_VERSION=$("${PYTHON}" --version 2>&1)
info "Python : ${PYTHON_VERSION} ($(command -v "${PYTHON}"))"

# ── pip install ───────────────────────────────────────────────────────────────
header "Step 1 — Installing Python dependencies"
"${PYTHON}" -m pip install --quiet --upgrade pip
"${PYTHON}" -m pip install --quiet -r "${REPO_ROOT}/requirements.txt"
success "Python dependencies installed."

# ── Rust wheel (maturin) ──────────────────────────────────────────────────────
WHEEL_DIR="${REPO_ROOT}/target/wheels"
WHEEL_PRESENT=false
if ls "${WHEEL_DIR}"/*.whl 2>/dev/null | grep -q .; then
  WHEEL_PRESENT=true
fi

if $NO_BUILD && $WHEEL_PRESENT; then
  info "Skipping Rust build (--no-build; existing wheel found)."
elif ! command -v maturin &>/dev/null; then
  header "Step 2 — Installing maturin + building Rust wheel"
  "${PYTHON}" -m pip install --quiet "maturin==1.7.8"
  cd "${REPO_ROOT}"
  maturin build --release --quiet
  "${PYTHON}" -m pip install --quiet --force-reinstall "${WHEEL_DIR}"/*.whl
  success "Rust wheel built and installed."
else
  header "Step 2 — Building Rust wheel"
  cd "${REPO_ROOT}"
  maturin build --release --quiet
  "${PYTHON}" -m pip install --quiet --force-reinstall "${WHEEL_DIR}"/*.whl
  success "Rust wheel built and installed."
fi

# ── Verify core modules load ──────────────────────────────────────────────────
header "Step 3 — Module self-check"
cd "${REPO_ROOT}"
"${PYTHON}" - <<'PYEOF'
import sys
sys.path.insert(0, "python")
ok = True
modules = [
    "apex_omega_core.core.types",
    "apex_omega_core.core.slippage_sentinel",
    "apex_omega_core.core.ssot_pipeline",
    "apex_omega_core.strategies.execution_router",
]
for m in modules:
    try:
        __import__(m)
        print(f"  \033[32m✓\033[0m {m}")
    except Exception as exc:
        print(f"  \033[31m✗\033[0m {m}  — {exc}", file=sys.stderr)
        ok = False

try:
    import apex_omega_core_rust
    print(f"  \033[35m✓\033[0m apex_omega_core_rust (Rust extension)")
except Exception as exc:
    print(f"  \033[33m⚠\033[0m apex_omega_core_rust not loaded — {exc}", file=sys.stderr)

if not ok:
    sys.exit(1)
PYEOF
success "Core modules verified."

# ── Write PID file helper ─────────────────────────────────────────────────────
> "${PID_FILE}"   # truncate / create

register_pid() {
  local label="$1" pid="$2"
  echo "${label}=${pid}" >> "${PID_FILE}"
}

# ── Cleanup on exit ───────────────────────────────────────────────────────────
_CLEANED_UP=false
_cleanup() {
  $CLEANED_UP && return   # idempotent — prevent double-fire on EXIT after signal
  _CLEANED_UP=true
  echo ""
  header "Shutting down Apex-Omega-v6"
  if [ -f "${PID_FILE}" ]; then
    while IFS='=' read -r label pid; do
      if kill -0 "${pid}" 2>/dev/null; then
        info "Stopping ${label} (PID ${pid}) …"
        kill -TERM "${pid}" 2>/dev/null || true
      fi
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
  fi
  success "All processes stopped.  Goodbye."
}
trap '_cleanup; exit 130' INT
trap '_cleanup; exit 143' TERM

# ── Start dashboard ───────────────────────────────────────────────────────────
if ! $BOT_ONLY; then
  header "Step 4 — Starting dashboard server  (port 5000)"
  export PYTHONPATH="${REPO_ROOT}/python:${REPO_ROOT}:${PYTHONPATH:-}"
  # Bind address: default 127.0.0.1 (localhost). Override via DASHBOARD_BIND env var.
  DASHBOARD_BIND="${DASHBOARD_BIND:-127.0.0.1}:5000"
  # Prefer gunicorn for production; fall back to Flask dev server.
  if command -v gunicorn &>/dev/null; then
    gunicorn \
      --bind "${DASHBOARD_BIND}" \
      --workers 2 \
      --worker-class sync \
      --timeout 120 \
      --reuse-port \
      --access-logfile "${DASHBOARD_LOG}" \
      --error-logfile "${DASHBOARD_LOG}" \
      app:app \
      &
  else
    FLASK_APP="${REPO_ROOT}/app.py" \
    FLASK_ENV=production \
    "${PYTHON}" "${REPO_ROOT}/app.py" >> "${DASHBOARD_LOG}" 2>&1 &
  fi
  DASHBOARD_PID=$!
  register_pid "dashboard" "${DASHBOARD_PID}"

  # Wait up to 10 s for the process to confirm it is running.
  for _i in 1 2 3 4 5; do
    sleep 2
    kill -0 "${DASHBOARD_PID}" 2>/dev/null && break
    if [ "${_i}" -eq 5 ]; then
      error "Dashboard failed to start after 10 s. Check ${DASHBOARD_LOG} for details."
      exit 1
    fi
  done
  success "Dashboard running — http://${DASHBOARD_BIND}  (PID ${DASHBOARD_PID})"
  success "Dashboard logs  — ${DASHBOARD_LOG}"
fi

# ── Start arbitrage bot ───────────────────────────────────────────────────────
if ! $DASHBOARD_ONLY; then
  header "Step 5 — Starting Apex-Omega arbitrage bot"
  BOT_SCRIPT="${REPO_ROOT}/python/polygon_arbitrage_bot.py"
  if [ ! -f "${BOT_SCRIPT}" ]; then
    error "Bot script not found: ${BOT_SCRIPT}"
    exit 1
  fi
  export PYTHONPATH="${REPO_ROOT}/python:${REPO_ROOT}:${PYTHONPATH:-}"
  "${PYTHON}" "${BOT_SCRIPT}" >> "${BOT_LOG}" 2>&1 &
  BOT_PID=$!
  register_pid "bot" "${BOT_PID}"

  # Wait up to 10 s for the process to confirm it is running.
  for _i in 1 2 3 4 5; do
    sleep 2
    kill -0 "${BOT_PID}" 2>/dev/null && break
    if [ "${_i}" -eq 5 ]; then
      error "Arbitrage bot failed to start after 10 s. Check ${BOT_LOG} for details."
      exit 1
    fi
  done
  success "Arbitrage bot running (PID ${BOT_PID})"
  success "Bot logs — ${BOT_LOG}"
fi

# ── Live status tail ──────────────────────────────────────────────────────────
header "System Online"
echo ""
echo -e "  ${GREEN}Dashboard${NC} : http://localhost:5000"
echo -e "  ${GREEN}Bot log  ${NC} : tail -f ${BOT_LOG}"
echo -e "  ${GREEN}Dash log ${NC} : tail -f ${DASHBOARD_LOG}"
echo -e "  ${YELLOW}Stop     ${NC} : ./stop.sh  or  Ctrl+C"
echo ""
info "Streaming bot output (Ctrl+C to stop all services) …"
echo ""

# Tail both logs to stdout so the operator sees live activity.
if ! $DASHBOARD_ONLY && ! $BOT_ONLY; then
  tail -f "${BOT_LOG}" "${DASHBOARD_LOG}" &
  TAIL_PID=$!
  register_pid "tail" "${TAIL_PID}"
elif ! $DASHBOARD_ONLY; then
  tail -f "${BOT_LOG}" &
  TAIL_PID=$!
  register_pid "tail" "${TAIL_PID}"
else
  tail -f "${DASHBOARD_LOG}" &
  TAIL_PID=$!
  register_pid "tail" "${TAIL_PID}"
fi

# Wait for the bot (or dashboard if bot-only) so the script stays alive.
if ! $DASHBOARD_ONLY; then
  wait "${BOT_PID}" || true
else
  wait "${DASHBOARD_PID}" || true
fi
