#!/bin/bash
# JARVIS one-command installer — macOS only.
# Installs: Homebrew (if missing), Python 3.12, Python deps, Ollama, models.
# Everything is free. Nothing phones home.
set -euo pipefail

echo "=== JARVIS installer ==="

# 1. Homebrew (package manager; needed for portaudio + ollama + python)
if ! command -v brew &>/dev/null; then
  echo "[1/6] Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
  echo "[1/6] Homebrew already installed."
fi

# 2. Python 3.12 — REQUIRED. onnxruntime (wake word engine) has no wheels
#    for Python 3.13+. macOS ships 3.13+ now, so we install 3.12 via brew.
#    SECURITY: We pin the interpreter so dependency resolution is
#    deterministic — no surprise breakage from a Python upgrade.
echo "[2/6] Ensuring Python 3.12..."
if ! brew list python@3.12 &>/dev/null; then
  brew install python@3.12
fi
PY312="$(brew --prefix python@3.12)/bin/python3.12"
if [ ! -x "$PY312" ]; then
  echo "ERROR: python3.12 not found at $PY312"
  exit 1
fi
echo "    Using: $PY312 ($("$PY312" --version))"

# 3. System deps: portaudio (mic), ollama (local LLM brain)
echo "[3/6] Installing portaudio + ollama..."
brew install portaudio ollama || true

# 4. Python virtualenv — SECURITY: isolated env so JARVIS deps never
#    touch your system Python or other projects.
echo "[4/6] Creating virtualenv with Python 3.12..."
rm -rf ~/.jarvis-venv  # clean slate if a broken 3.13 venv exists
"$PY312" -m venv ~/.jarvis-venv
# shellcheck disable=SC1091
source ~/.jarvis-venv/bin/activate
echo "    venv python: $(python --version)"

# 5. Python deps
echo "[5/6] Installing Python packages (this takes a few minutes)..."
pip install --upgrade "pip<25"  # pip 25+ drops 3.12-era wheel tags we need
pip install -r "$(dirname "$0")/backend/requirements.txt"

# 6. Start Ollama + pull the LLM brain (~5GB, one-time, then 100% offline).
#    qwen3:8b is free (Apache-2.0). On an 8 GB Mac, set brain: model:
#    "llama3.2:3b" in config/jarvis.yaml instead.
echo "[6/6] Starting Ollama and pulling local LLM (qwen3:8b)..."
if ! curl -sf -o /dev/null http://127.0.0.1:11434/api/tags 2>/dev/null; then
  echo "    Starting ollama serve in background..."
  nohup ollama serve > /tmp/ollama.log 2>&1 &
  for i in $(seq 1 20); do
    curl -sf -o /dev/null http://127.0.0.1:11434/api/tags 2>/dev/null && break
    sleep 1
  done
fi
ollama pull qwen3:8b || echo "Ollama pull failed — run 'ollama pull qwen3:8b' manually later."

# Wake-word model files (~4 MB, one time). openWakeWord's pip package
# doesn't include them. Non-fatal: the voice loop retries on its own.
echo "    Fetching wake-word models..."
(cd "$(dirname "$0")" && python -c "from backend.voice.wake import ensure_models; ensure_models()") \
  || echo "    (Wake-word models will download on first launch.)"

echo ""
echo "=== Done ==="
echo "Open JARVIS.app. Grant Microphone permission when macOS asks."
echo "Say 'Jarvis' (or 'Hey Jarvis') and give a command."
