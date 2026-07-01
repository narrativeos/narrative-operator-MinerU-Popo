#!/usr/bin/env bash
set -euo pipefail

# macOS-compatible case-insensitive comparison (bash 3.2 compatible)
to_lower() {
  echo "$1" | tr '[:upper:]' '[:lower:]'
}

# Usage:
#   bash scripts/start_popo_local.sh [api|worker|all|--help]
# Examples:
#   bash scripts/start_popo_local.sh          # Default: start API server with background worker
#   bash scripts/start_popo_local.sh api
#   bash scripts/start_popo_local.sh worker
#   bash scripts/start_popo_local.sh all

MODE="${1:-api}"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# Auto-activate virtual environment if it exists
if [[ -d ".venv" ]]; then
  echo "[info] activating virtual environment: .venv"
  source .venv/bin/activate
elif command -v uv &>/dev/null && uv venv --help &>/dev/null; then
  echo "[info] creating uv environment..."
  uv venv
  source .venv/bin/activate
fi

# Detect platform
detect_platform() {
  local arch
  arch=$(uname -m)
  local os_name
  os_name=$(uname -s)

  if [[ "$os_name" == "Darwin" ]]; then
    echo "macos"
  elif [[ "$arch" == "x86_64" || "$arch" == "aarch64" ]]; then
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null 2>&1; then
      echo "linux-gpu"
    else
      echo "linux-cpu"
    fi
  fi
}

# Check if core dependencies are available (bash 3.2 compatible - no arrays)
ensure_project_installed() {
  local needs_install=false

  # Check core Python packages
  if ! python -c "import fastapi" 2>/dev/null; then
    needs_install=true
  fi
  if ! python -c "import uvicorn" 2>/dev/null; then
    needs_install=true
  fi
  if ! python -c "import transformers" 2>/dev/null; then
    needs_install=true
  fi

  if [ "$needs_install" = false ]; then
    echo "[ok] All dependencies are already installed"
    return 0
  fi

  echo "[info] Project dependencies not found, installing..."

  local install_cmd=""
  if [[ -d ".venv" ]] && command -v uv &>/dev/null; then
    install_cmd="uv pip install"
  elif command -v pip &>/dev/null; then
    install_cmd="pip install"
  else
    install_cmd="python -m pip install"
  fi

  local platform
  platform="$(detect_platform)"
  echo "[info] Detected platform: $platform"

  # Install API dependencies first (always required)
  echo "[info] Installing API dependencies..."
  $install_cmd -r api/requirements.txt || {
    echo "[error] Failed to install API dependencies." >&2
    echo "        Please install manually: $install_cmd -r api/requirements.txt" >&2
    exit 1
  }

  # Platform-specific main dependencies installation
  if [[ "$platform" == "macos" ]]; then
    echo "[info] macOS detected, filtering out GPU-specific dependencies..."
    local filtered_reqs
    filtered_reqs=$(mktemp)
    # Remove CUDA/NVIDIA/GPU-only packages that don't work on macOS
    grep -v -E '^(cuda-bindings|cuda-pathfinder|cupy-|nvidia-|triton==)' requirements.txt > "$filtered_reqs" 2>/dev/null || cp requirements.txt "$filtered_reqs"
    echo "[info] Installing filtered dependencies (GPU packages excluded)..."
    $install_cmd -r "$filtered_reqs" || {
      echo "[warn] Some dependencies may have failed to install." >&2
      echo "       GPU-related packages are not available on macOS." >&2
      echo "       Core API functionality should still work." >&2
    }
    rm -f "$filtered_reqs"
  else
    echo "[info] Installing full dependencies..."
    $install_cmd -r requirements.txt || {
      echo "[error] Failed to install project dependencies." >&2
      echo "        Please install manually: $install_cmd -r requirements.txt" >&2
      exit 1
    }
  fi

  echo "[ok] Dependencies installed successfully"
}

ensure_project_installed

# Configure environment variables
export POPO_MODEL_PATH="${POPO_MODEL_PATH:-$ROOT_DIR/models/Mineru-Popo}"
export POPO_SQLITE_PATH="${POPO_SQLITE_PATH:-$ROOT_DIR/data/popo_tasks.db}"
export POPO_API_HOST="${POPO_API_HOST:-0.0.0.0}"
export POPO_API_PORT="${POPO_API_PORT:-8440}"
export POPO_WORKER_CONCURRENCY="${POPO_WORKER_CONCURRENCY:-4}"
export POPO_SYNC_TIMEOUT="${POPO_SYNC_TIMEOUT:-300}"
export POPO_TASK_TTL="${POPO_TASK_TTL:-86400}"

# Port configuration
PORT_SCAN_SPAN="${POPO_PORT_SCAN_SPAN:-9}"

find_free_port() {
  local start_port="$1"
  local span="$2"
  local end_port=$((start_port + span))
  local port

  for ((port = start_port; port <= end_port; port++)); do
    if python - "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", port))
    print(port)
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
    then
      echo "$port"
      return 0
    fi
  done

  return 1
}

pick_port() {
  local preferred_port="$1"
  local service_name="$2"
  local selected_port

  selected_port="$(find_free_port "$preferred_port" "$PORT_SCAN_SPAN")" || {
    echo "[error] no free port found for ${service_name} in range ${preferred_port}-$((preferred_port + PORT_SCAN_SPAN))" >&2
    exit 1
  }

  if [[ "$selected_port" != "$preferred_port" ]]; then
    echo "[warn] ${service_name} preferred port :${preferred_port} is busy, using :${selected_port}" >&2
  fi

  echo "$selected_port"
}

start_api() {
  local selected_port
  selected_port="$(pick_port "$POPO_API_PORT" "api")"
  export POPO_API_PORT="$selected_port"
  echo "[start] api on :${selected_port}"
  echo "[info] docs: http://localhost:${selected_port}/docs"
  python -m api.main
}

start_worker() {
  echo "[start] worker (concurrency: ${POPO_WORKER_CONCURRENCY})"
  python -c "from api.services.worker import run_worker; run_worker()"
}

print_usage() {
  echo "Usage: bash scripts/start_popo_local.sh [api|worker|all]"
  echo ""
  echo "Modes:"
  echo "  api     - Start FastAPI server (with background worker, default)"
  echo "  worker  - Start background worker only (separate process)"
  echo "  all     - Start API + independent worker in background"
  echo ""
  echo "Environment Variables:"
  echo "  POPO_MODEL_PATH         - Model path (default: ./models/Mineru-Popo)"
  echo "  POPO_SQLITE_PATH        - SQLite database path (default: ./data/popo_tasks.db)"
  echo "  POPO_API_HOST           - Server host (default: 0.0.0.0)"
  echo "  POPO_API_PORT           - Server port (default: 8440)"
  echo "  POPO_WORKER_CONCURRENCY - Worker concurrency (default: 4)"
  echo "  POPO_SYNC_TIMEOUT       - Sync timeout in seconds (default: 300)"
  echo "  POPO_TASK_TTL           - Task TTL in seconds (default: 86400)"
}

if [[ "$MODE" == "--help" || "$MODE" == "-h" ]]; then
  print_usage
  exit 0
fi

if [[ -z "${CONDA_DEFAULT_ENV:-}" ]]; then
  echo "[warn] No conda env detected in this shell."
  echo "       Recommended: conda activate popo"
fi

echo "[info] working dir: $ROOT_DIR"
echo "[info] python: $(command -v python)"
echo "[info] model path: $POPO_MODEL_PATH"
echo "[info] sqlite path: $POPO_SQLITE_PATH"
echo "[info] api host: $POPO_API_HOST"
echo "[info] worker concurrency: $POPO_WORKER_CONCURRENCY"

case "$MODE" in
  api)
    start_api
    ;;
  worker)
    start_worker
    ;;
  all)
    selected_port="$(pick_port "$POPO_API_PORT" "api")"
    export POPO_API_PORT="$selected_port"

    pids=()

    cleanup() {
      echo
      echo "[info] stopping all services..."
      for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
          kill "$pid" 2>/dev/null || true
        fi
      done
      wait || true
      echo "[ok] all services stopped"
    }

    trap cleanup INT TERM

    echo "[start] worker (concurrency: ${POPO_WORKER_CONCURRENCY})"
    python -c "from api.services.worker import run_worker; run_worker()" &
    pids+=("$!")

    echo "[start] api on :${selected_port}"
    python -m api.main &
    pids+=("$!")

    echo "[ok] started all services in foreground-managed mode"
    echo "[info] docs: http://localhost:${selected_port}/docs"
    echo "[info] press Ctrl+C to stop all"

    wait
    ;;
  *)
    echo "Unknown mode: $MODE"
    print_usage
    exit 1
    ;;
esac