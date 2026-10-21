#!/bin/bash
# JARVIS — self-contained macOS app.
# Double-click → native app window opens. No browser, no Terminal.
#
# v1.0.5 launcher. A copy of this file also lives at
# Contents/Resources/app/launcher.sh; the app keeps the two in sync so
# the in-app updater can deliver launcher fixes too.
#
# NOTE: deliberately NOT `set -e`. One failing optional step (Ollama pull,
# mic check, offline updater) must never stop JARVIS from opening.
set -uo pipefail

# macOS .app launches get a minimal PATH — add Homebrew + system paths
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONUNBUFFERED=1   # logs appear in /tmp/jarvis-*.log immediately

# All code is bundled inside the app.
APP_ROOT="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
VENV="$HOME/.jarvis-venv"
REQ="$APP_ROOT/backend/requirements.txt"
REQ_STAMP="$VENV/.jarvis-requirements.sha"
LAUNCH_LOG="/tmp/jarvis-launch.log"
# Updates from the public GitHub repo. No auth needed.
GH_ZIP_URL="https://github.com/monil-afk/jarvis/archive/refs/heads/main.zip"
GH_VER_URL="https://raw.githubusercontent.com/monil-afk/jarvis/main/JARVIS.app/Contents/Resources/app/version.txt"

exec > >(tee -a "$LAUNCH_LOG") 2>&1
echo "=== JARVIS launch $(date) ==="

dialog() {  # dialog "message" — simple macOS popup (no Terminal needed)
  osascript -e "display dialog \"$1\" buttons {\"OK\"} default button \"OK\" with title \"JARVIS\"" >/dev/null 2>&1 || true
}

# --- Auto-updater ---
# Checks GitHub for a new version on every launch. If found, downloads the
# repo zip, replaces the app code AND this launcher, and restarts.
# Silent when up to date or offline — never blocks startup.
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
    local new_launcher="$tmp_dir/jarvis-main/JARVIS.app/Contents/MacOS/JARVIS"
    if [ -f "$new_app/version.txt" ]; then
      cp -R "$new_app/"* "$APP_ROOT/"
      if [ -f "$new_launcher" ]; then
        # Replace this script atomically (copy then rename). Overwriting a
        # running bash script in place can make bash execute garbage.
        cp "$new_launcher" "$SELF.new" && chmod 755 "$SELF.new" && mv -f "$SELF.new" "$SELF"
      fi
      echo "Updated to $remote_ver. Restarting JARVIS..."
      rm -rf "$tmp_zip" "$tmp_dir"
      exec "$SELF" "$@"
    fi
  fi
  rm -rf "$tmp_zip" "$tmp_dir"
  echo "(Update failed — continuing on $current_ver.)"
}
check_for_updates "$@"

# First launch: set up the Python environment (one time)
if [ ! -x "$VENV/bin/python3" ]; then
  dialog "Welcome to JARVIS. First launch installs everything (about 5 minutes). Click OK to begin."
  bash "$APP_ROOT/install.sh"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Install Python packages ONLY when requirements.txt changed (or something
# critical is missing). v1.0.4 checked for `websockets`, which isn't in
# requirements.txt, so it reinstalled on every single launch.
req_hash=$(shasum "$REQ" 2>/dev/null | awk '{print $1}')
if [ "$(cat "$REQ_STAMP" 2>/dev/null)" != "$req_hash" ] \
   || ! python3 -c "import fastapi, uvicorn, webview, sounddevice, openwakeword, onnxruntime, yaml" 2>/dev/null; then
  echo "Installing / updating Python packages..."
  if pip install -r "$REQ"; then
    echo "$req_hash" > "$REQ_STAMP"
  fi
fi

cd "$APP_ROOT" || exit 1

# Two copies of JARVIS.app (e.g. one in Downloads, one installed) must not
# run at once: each launcher would stop the other's voice loop and JARVIS
# would go deaf. If another copy is already running, leave it alone.
for pid in $(pgrep -f "backend\.native_app" 2>/dev/null); do
  other=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
  if [ -n "$other" ] && [ "$other" != "$APP_ROOT" ]; then
    echo "JARVIS already running from $other — not starting a second copy."
    dialog "JARVIS is already running from another copy:\n${other%/Contents/Resources/app}\n\nOnly one copy can listen at a time. Quit that one first if you want to use this one."
    exit 0
  fi
done

# Stop anything left over from a previous launch (server, voice loop, window).
STALE_PIDS=$(lsof -ti:8765 2>/dev/null || true)
if [ -n "$STALE_PIDS" ]; then
  echo "$STALE_PIDS" | xargs kill -9 2>/dev/null || true
fi
pkill -f "backend.server" 2>/dev/null || true
pkill -f "backend.voice.loop" 2>/dev/null || true
sleep 1

# Make sure Ollama (the brain) is running
if ! curl -sf -o /dev/null http://127.0.0.1:11434/api/tags 2>/dev/null; then
  if command -v ollama >/dev/null 2>&1; then
    nohup ollama serve > /tmp/ollama.log 2>&1 &
    for _ in $(seq 1 20); do
      curl -sf -o /dev/null http://127.0.0.1:11434/api/tags 2>/dev/null && break
      sleep 1
    done
  else
    echo "WARNING: ollama not found — JARVIS will hear you but can't think."
  fi
fi

# Make sure the AI brain model is downloaded (once per model).
# The model is set in config/jarvis.yaml → brain: model:
BRAIN_MODEL=$(python3 -c "import yaml;print(((yaml.safe_load(open('config/jarvis.yaml')) or {}).get('brain') or {}).get('model') or 'llama3.2:3b')" 2>/dev/null || echo "llama3.2:3b")
[ -z "$BRAIN_MODEL" ] && BRAIN_MODEL="llama3.2:3b"
case "$BRAIN_MODEL" in *:*) ;; *) BRAIN_MODEL="$BRAIN_MODEL:latest" ;; esac
if command -v ollama >/dev/null 2>&1 && ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$BRAIN_MODEL"; then
  dialog "JARVIS is downloading its brain ($BRAIN_MODEL). This happens once and can take a few minutes. Click OK to continue."
  ollama pull "$BRAIN_MODEL" || echo "WARNING: could not pull $BRAIN_MODEL (offline?)"
fi

# Microphone permission. Runs until access is confirmed once, then never
# again (marker file). Opening the mic from this process makes macOS
# show "JARVIS would like to access the microphone".
MIC_MARKER="$HOME/.jarvis/.mic_ok"
mkdir -p "$HOME/.jarvis"
if [ ! -f "$MIC_MARKER" ]; then
  if python3 -m backend.mic_onboarding 2>&1 | tee /tmp/jarvis-mic.log; then
    touch "$MIC_MARKER"
  else
    dialog "JARVIS can't hear you yet. Open System Settings > Privacy & Security > Microphone, switch ON JARVIS, then quit and reopen JARVIS."
  fi
fi

# Start the HUD server in the background (localhost only)
python3 -u -m backend.server > /tmp/jarvis-server.log 2>&1 &
SERVER_PID=$!

# Start the voice loop in the background
python3 -u -m backend.voice.loop > /tmp/jarvis-voice.log 2>&1 &
VOICE_PID=$!

# Cleanup: stop server + voice when the window closes (or the app is quit).
cleanup() {
  kill "$VOICE_PID" "$SERVER_PID" 2>/dev/null || true
  sleep 1
  kill -9 "$VOICE_PID" "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

# Open the NATIVE app window (blocks until the window is closed).
# NOT `exec` — exec would replace this script and the cleanup trap above
# would never run, leaving old voice loops listening in the background.
python3 -u -m backend.native_app
