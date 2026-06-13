#!/usr/bin/env bash
# =============================================================================
#  Apex-Omega-v6  â€”  One-Click Boot
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
#    that file or the shell â€” they are never hardcoded here.
#
#  Logs
#    dashboard.log   â€” Flask / gunicorn output
#    bot.log         â€” Arbitrage bot output
#    PIDs written to .apex_pids so stop.sh can find them.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${REPO_ROOT}/.apex_pids"
DASHBOARD_LOG="${REPO_ROOT}/dashboard.log"
BOT_LOG="${REPO_ROOT}/bot.log"

# â”€â”€ Colour helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[APEX]${NC} $*"; }
success() { echo -e "${GREEN}[APEX]${NC} $*"; }
warn()    { echo -e "${YELLOW}[APEX]${NC} $*"; }
error()   { echo -e "${RED}[APEX]${NC} $*" >&2; }
header()  { echo -e "\n${BOLD}${BLUE}â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•${NC}"; \
            echo -e "${BOLD}${BLUE}  $*${NC}"; \
            echo -e "${BOLD}${BLUE}â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•${NC}"; }

# â”€â”€ Argument parsing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€ Banner â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
header "Apex-Omega-v6  /  Full System Boot"
info "Timestamp : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
info "Repo root : ${REPO_ROOT}"

# â”€â”€ Load .env (shell vars take precedence) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
  warn ".env file not found â€” relying on shell environment variables only."
fi

# â”€â”€ Safety: default LIVE_EXECUTION to false â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if $DRY_RUN; then
  export LIVE_EXECUTION=false
  export LIVE_TRADING_ENABLED=false
  export DRY_RUN=true
  export ARM_LIVE_EXECUTION=false
  export APEX_SEND_TX=0
  warn "DRY-RUN mode: LIVE_EXECUTION=false, APEX_SEND_TX=0"
else
  # If not explicitly armed, default to shadow/dry-run.
  : "${LIVE_EXECUTION:=false}"
  : "${LIVE_TRADING_ENABLED:=${LIVE_EXECUTION}}"
  : "${DRY_RUN:=false}"
  : "${ARM_LIVE_EXECUTION:=false}"
  : "${APEX_SEND_TX:=0}"
  if [ "${LIVE_EXECUTION}" = "true" ] || [ "${LIVE_TRADING_ENABLED}" = "true" ]; then
    export LIVE_EXECUTION=true LIVE_TRADING_ENABLED=true DRY_RUN=false ARM_LIVE_EXECUTION=true APEX_SEND_TX=1
    warn "LIVE EXECUTION IS ENABLED â€” real transactions may be submitted."
  else
    export LIVE_EXECUTION=false LIVE_TRADING_ENABLED=false DRY_RUN=true ARM_LIVE_EXECUTION=false APEX_SEND_TX=0
    info "Shadow mode active (LIVE_EXECUTION=false)"
  fi
fi

# Normalize the live-execution aliases expected by the runtime config and
# contract invokers.  Shell values win if already provided explicitly.
: "${EXECUTOR_PRIVATE_KEY:=${PRIVATE_KEY:-}}"
: "${APEX_PRIVATE_KEY:=${EXECUTOR_PRIVATE_KEY:-}}"
: "${AAVE_V3_POOL_ADDRESS:=${AAVE_POOL_ADDRESS:-}}"
: "${BALANCER_VAULT_ADDRESS:=${BALANCER_VAULT:-}}"
: "${C1_INSTITUTIONAL_EXECUTOR_ADDRESS:=0x05c43ef06057F1fb8FCA7E76dC2029a366deC225}"
: "${C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS:=0x8B04b0db6e803Bc29C3327885351D4297ABad9BE}"
: "${LIQUIDATION_EXECUTOR_ADDRESS:=0xF9a28f389Ad8c33F9da68c736BEAf1F2A3795a56}"
export EXECUTOR_PRIVATE_KEY APEX_PRIVATE_KEY AAVE_V3_POOL_ADDRESS BALANCER_VAULT_ADDRESS
export C1_INSTITUTIONAL_EXECUTOR_ADDRESS C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS LIQUIDATION_EXECUTOR_ADDRESS

if [ "${LIVE_EXECUTION}" = "true" ] || [ "${LIVE_TRADING_ENABLED}" = "true" ]; then
  missing_live=()
  [ -z "${POLYGON_RPC:-}" ] && missing_live+=("POLYGON_RPC")
  [ -z "${EXECUTOR_PRIVATE_KEY:-}" ] && missing_live+=("EXECUTOR_PRIVATE_KEY")
  [ -z "${C1_INSTITUTIONAL_EXECUTOR_ADDRESS:-}" ] && missing_live+=("C1_INSTITUTIONAL_EXECUTOR_ADDRESS")
  [ -z "${C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS:-}" ] && missing_live+=("C2_ULTIMATE_ARBITRAGE_EXECUTOR_ADDRESS")
  [ -z "${LIQUIDATION_EXECUTOR_ADDRESS:-}" ] && missing_live+=("LIQUIDATION_EXECUTOR_ADDRESS")
  [ -z "${AAVE_V3_POOL_ADDRESS:-}" ] && missing_live+=("AAVE_V3_POOL_ADDRESS")
  if [ "${#missing_live[@]}" -ne 0 ]; then
    error "Live execution requested but missing: ${missing_live[*]}"
    error "Populate the env or run with --dry-run."
    exit 1
  fi
fi

# â”€â”€ RPC endpoint (polygon.drpc.org is in the repo's allowed domain list) â”€â”€â”€â”€â”€â”€
RPC_URL="${POLYGON_RPC:-${POLYGON_HTTP:-${APEX_RPC_URL:-}}}"
if [ -z "${RPC_URL}" ]; then
  warn "No RPC URL found in environment â€” falling back to public polygon.drpc.org"
  warn "Set POLYGON_RPC in your .env for production use."
  RPC_URL="https://polygon.drpc.org"
fi
export POLYGON_RPC="${RPC_URL}"
export APEX_RPC_URL="${RPC_URL}"
info "RPC endpoint : ${RPC_URL}"

# â”€â”€ Python interpreter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€ pip install â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
header "Step 1 â€” Installing Python dependencies"
"${PYTHON}" -m pip install --quiet --upgrade pip
"${PYTHON}" -m pip install --quiet -r "${REPO_ROOT}/requirements.txt"
success "Python dependencies installed."

# â”€â”€ Rust wheel (maturin) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
WHEEL_DIR="${REPO_ROOT}/target/wheels"
WHEEL_PRESENT=false
if ls "${WHEEL_DIR}"/*.whl 2>/dev/null | grep -q .; then
  WHEEL_PRESENT=true
fi

if $NO_BUILD && $WHEEL_PRESENT; then
  info "Skipping Rust build (--no-build; existing wheel found)."
elif ! command -v maturin &>/dev/null; then
  header "Step 2 â€” Installing maturin + building Rust wheel"
  "${PYTHON}" -m pip install --quiet "maturin==1.7.8"
  cd "${REPO_ROOT}"
  maturin build --release --quiet
  "${PYTHON}" -m pip install --quiet --force-reinstall "${WHEEL_DIR}"/*.whl
  success "Rust wheel built and installed."
else
  header "Step 2 â€” Building Rust wheel"
  cd "${REPO_ROOT}"
  maturin build --release --quiet
  "${PYTHON}" -m pip install --quiet --force-reinstall "${WHEEL_DIR}"/*.whl
  success "Rust wheel built and installed."
fi

# â”€â”€ Verify core modules load â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
header "Step 3 â€” Module self-check"
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
        print(f"  \033[32mâœ“\033[0m {m}")
    except Exception as exc:
        print(f"  \033[31mâœ—\033[0m {m}  â€” {exc}", file=sys.stderr)
        ok = False

try:
    import apex_omega_core_rust
    print(f"  \033[35mâœ“\033[0m apex_omega_core_rust (Rust extension)")
except Exception as exc:
    print(f"  \033[33mâš \033[0m apex_omega_core_rust not loaded â€” {exc}", file=sys.stderr)

if not ok:
    sys.exit(1)
PYEOF
success "Core modules verified."

# â”€â”€ Write PID file helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
> "${PID_FILE}"   # truncate / create

register_pid() {
  local label="$1" pid="$2"
  echo "${label}=${pid}" >> "${PID_FILE}"
}

# â”€â”€ Cleanup on exit â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_CLEANED_UP=false
_cleanup() {
  $CLEANED_UP && return   # idempotent â€” prevent double-fire on EXIT after signal
  _CLEANED_UP=true
  echo ""
  header "Shutting down Apex-Omega-v6"
  if [ -f "${PID_FILE}" ]; then
    while IFS='=' read -r label pid; do
      if kill -0 "${pid}" 2>/dev/null; then
        info "Stopping ${label} (PID ${pid}) â€¦"
        kill -TERM "${pid}" 2>/dev/null || true
      fi
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
  fi
  success "All processes stopped.  Goodbye."
}
trap '_cleanup; exit 130' INT
trap '_cleanup; exit 143' TERM

# â”€â”€ Start dashboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if ! $BOT_ONLY; then
  header "Step 4 â€” Starting dashboard server  (port 5000)"
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
  success "Dashboard running â€” http://${DASHBOARD_BIND}  (PID ${DASHBOARD_PID})"
  success "Dashboard logs  â€” ${DASHBOARD_LOG}"
fi

# â”€â”€ Start arbitrage bot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if ! $DASHBOARD_ONLY; then
  header "Step 5 â€” Starting Apex-Omega arbitrage bot"
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
  success "Bot logs â€” ${BOT_LOG}"
fi

# â”€â”€ Live status tail â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
header "System Online"
echo ""
echo -e "  ${GREEN}Dashboard${NC} : http://localhost:5000"
echo -e "  ${GREEN}Bot log  ${NC} : tail -f ${BOT_LOG}"
echo -e "  ${GREEN}Dash log ${NC} : tail -f ${DASHBOARD_LOG}"
echo -e "  ${YELLOW}Stop     ${NC} : ./stop.sh  or  Ctrl+C"
echo ""
info "Streaming bot output (Ctrl+C to stop all services) â€¦"
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
