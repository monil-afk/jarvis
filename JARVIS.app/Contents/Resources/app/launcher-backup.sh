#!/bin/bash
# JARVIS — self-contained macOS app.
# Double-click → native app window opens. No browser, no Terminal.
set -euo pipefail

# macOS .app launches get a minimal PATH — add Homebrew + system paths
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# All code is bundled inside the app.
APP_ROOT="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
VENV="$HOME/.jarvis-venv"
# Updates from the public GitHub repo. No auth needed.
# (Repo must exist — create it via publish.sh first.)
GH_ZIP_URL="https://github.com/monil-afk/jarvis/archive/refs/heads/main.zip"
GH_VER_URL="https://raw.githubusercontent.com/monil-afk/jarvis/main/JARVIS.app/Contents/Resources/app/version.txt"

# --- Auto-updater ---
# Checks GitHub for a new version on every launch. If found, downloads the
# repo zip, extracts the new app code, and restarts. Silent when up to date
# or offline — never blocks startup.
check_for_updates() {
  local current_ver remote_ver
  current_ver=$(tr -d '[:space:]' < "$APP_ROOT/version.txt" 2>/dev/null || echo "0.0.0")
  remote_ver=$(curl -sf --max-time 10 "$GH_VER_URL" 2>/dev/null | tr -d '[:space:]' || echo "")
  if [ -z "$remote_ver" ] || [ "$remote_ver" = "$current_ver" ]; then
    return 0  # up to date or offline — continue silently
  fi
  echo "JARVIS update available: $current_ver → $remote_ver. Downloading..."
  local tmp_zip="/tmp/jarvis-update.zip" tmp_dir="/tmp/jarvis-update"
  rm -rf "$tmp_zip" "$tmp_dir" && mkdir -p "$tmp_dir"
  if curl -sfL --max-time 180 -o "$tmp_zip" "$GH_ZIP_URL" 2>/dev/null \
     && unzip -qo "$tmp_zip" -d "$tmp_dir" 2>/dev/null; then
    local new_app="$tmp_dir/jarvis-main/JARVIS.app/Contents/Resources/app"
    if [ -f "$new_app/version.txt" ]; then
      cp -R "$new_app/"* "$APP_ROOT/"
      echo "Updated to $remote_ver. Restarting JARVIS..."
      rm -rf "$tmp_zip" "$tmp_dir"
      exec "$0" "$@"
    fi
  fi
  rm -rf "$tmp_zip" "$tmp_dir"
  echo "(Update failed — continuing on $current_ver.)"
}
check_for_updates "$@"

# First launch: set up the Python environment (one time)
if [ ! -d "$VENV" ]; then
  # Show a simple dialog via osascript since there's no Terminal
  osascript -e 'display dialog "Welcome to JARVIS. First launch installs everything (about 5 minutes). Click OK to begin." buttons {"OK"} default button "OK"' >/dev/null
  bash "$APP_ROOT/install.sh"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Verify critical deps (install pywebview if missing)
if ! python3 -c "import fastapi, uvicorn, webview, websockets" 2>/dev/null; then
  pip install -r "$APP_ROOT/backend/requirements.txt"
fi

cd "$APP_ROOT"

# Kill any stale server from a previous launch
STALE_PIDS=$(lsof -ti:8765 2>/dev/null || true)
if [ -n "$STALE_PIDS" ]; then
  echo "$STALE_PIDS" | xargs kill 2>/dev/null || true
  sleep 1
fi

# Make sure Ollama (the brain) is running
if ! curl -sf -o /dev/null http://127.0.0.1:11434/api/tags 2>/dev/null; then
  nohup ollama serve > /tmp/ollama.log 2>&1 &
  for i in $(seq 1 20); do
    curl -sf -o /dev/null http://127.0.0.1:11434/api/tags 2>/dev/null && break
    sleep 1
  done
fi

# Make sure the AI brain model is downloaded
if ! ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
  ollama pull llama3.2:3b
fi

# Start the HUD server in the background (localhost only)
python3 -m backend.server > /tmp/jarvis-server.log 2>&1 &
SERVER_PID=$!

# Start the voice loop in the background
python3 -m backend.voice.loop > /tmp/jarvis-voice.log 2>&1 &
VOICE_PID=$!

# Cleanup: kill server + voice when the app window closes
trap 'kill $SERVER_PID $VOICE_PID 2>/dev/null' EXIT

# Open the NATIVE app window (blocks until window closed)
# This is a real macOS Cocoa window, not a browser tab.
exec python3 -m backend.native_app
