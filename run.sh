#!/bin/bash
cd "$(dirname "$0")"

echo "================================================"
echo "  Open-LLM-VTuber ai陪伴"
echo "  DeepSeek + Qwen TTS + Sherpa-ONNX ASR"
echo "================================================"
echo ""

# Check Python version
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "[ERROR] Python 3 not found. Please install Python 3.11+ first."
    echo "  macOS: brew install python@3.12"
    exit 1
fi

echo "[INFO] System Python version: $PYTHON_VERSION"

# Check for uv package manager
if ! command -v uv &> /dev/null; then
    echo "[INFO] uv not found, installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if ! command -v uv &> /dev/null; then
    echo "[ERROR] uv installation finished, but the uv command is still unavailable."
    echo "[ERROR] Reopen Terminal and run this launcher again."
    exit 1
fi

echo "[INFO] uv version: $(uv --version)"

# Setup virtual environment and install dependencies
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment..."
    uv venv --directory "$(pwd)"
    echo "[INFO] Installing dependencies..."
    uv sync --directory "$(pwd)"
else
    echo "[INFO] Virtual environment found, syncing dependencies..."
    uv sync --directory "$(pwd)"
fi

# Fix sherpa-onnx onnxruntime linking (if needed)
SHERPA_LIB_DIR=".venv/lib/python3.11/site-packages/sherpa_onnx/lib"
ONNX_DYLIB=$(ls .venv/lib/python3.11/site-packages/onnxruntime/capi/libonnxruntime.*.dylib 2>/dev/null | head -1)
if [ -n "$ONNX_DYLIB" ]; then
    TARGET_LINK="$SHERPA_LIB_DIR/$(basename "$ONNX_DYLIB")"
    # Always fix the link: remove broken symlink or outdated file, then recreate
    if [ -L "$TARGET_LINK" ] || [ -f "$TARGET_LINK" ]; then
        rm -f "$TARGET_LINK"
    fi
    echo "[INFO] Fixing sherpa-onnx ↔ onnxruntime library link..."
    ln -s "../../onnxruntime/capi/$(basename "$ONNX_DYLIB")" "$TARGET_LINK" 2>/dev/null || \
    ln -s "$(pwd)/$ONNX_DYLIB" "$TARGET_LINK" 2>/dev/null || \
    echo "[WARN] Could not create symlink. You may need to run: ln -s \$(pwd)/$ONNX_DYLIB $SHERPA_LIB_DIR/"
fi

# Create required directories
mkdir -p logs
mkdir -p chat_history
mkdir -p cache

echo ""
echo "[INFO] Starting server..."
echo "[INFO] Browser will open automatically at http://localhost:12393"
echo ""

# Do not silently open an older server when this launch cannot own the port.
if nc -z localhost 12393 2>/dev/null; then
  echo "[ERROR] Port 12393 is already in use by another backend."
  echo "[ERROR] Stop the existing process before starting a new one."
  exit 1
fi

# Auto-open the browser only after the backend can actually serve HTTP.
# The waiter is cleaned up automatically if the backend fails or is stopped.
BROWSER_WAITER_PID=""
cleanup_browser_waiter() {
  if [ -n "$BROWSER_WAITER_PID" ]; then
    kill "$BROWSER_WAITER_PID" 2>/dev/null || true
  fi
}
trap cleanup_browser_waiter EXIT INT TERM

(
  echo "[INFO] Waiting for the server to accept HTTP requests on port 12393..."
  while true; do
    if curl --noproxy '*' --silent --fail --output /dev/null \
      --connect-timeout 1 --max-time 2 http://127.0.0.1:12393/; then
      echo "[INFO] Server is ready, opening browser..."
      open http://localhost:12393
      exit 0
    fi
    sleep 1
  done
) &
BROWSER_WAITER_PID=$!

# Run the server (pass through all arguments)
uv run --directory "$(pwd)" run_server.py "$@"
