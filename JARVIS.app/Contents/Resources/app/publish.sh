#!/bin/bash
# JARVIS one-command GitHub publisher.
# Run this ONCE on your Mac:  bash publish.sh
# It installs the GitHub CLI (if needed), logs you in via browser,
# creates the public `jarvis` repo, and pushes all the code.
set -euo pipefail

echo "=== JARVIS GitHub publisher ==="

# 1. GitHub CLI
if ! command -v gh &>/dev/null; then
  echo "[1/4] Installing GitHub CLI..."
  brew install gh
else
  echo "[1/4] GitHub CLI already installed."
fi

# 2. Login (opens browser once)
if ! gh auth status &>/dev/null; then
  echo "[2/4] Logging into GitHub (browser will open)..."
  gh auth login --web --git-protocol https
else
  echo "[2/4] Already logged into GitHub."
fi

# 3. Create the public repo (skips if it exists)
if gh repo view jarvis &>/dev/null; then
  echo "[3/4] Repo 'jarvis' already exists."
else
  echo "[3/4] Creating public repo 'jarvis'..."
  gh repo create jarvis --public \
    --description "JARVIS — your local voice agent. Talk to your Mac, it listens, understands, and acts. 100% local, 100% free." \
    --clone=false
fi

# 4. Push this code
echo "[4/4] Pushing code..."
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
# fresh git history for the public repo
rm -rf .git
git init -q
git add -A
git -c user.name="$(gh api user -q .login)" commit -qm "JARVIS — local voice agent for Mac (initial release)"
git branch -M main
git remote add origin "$(gh repo view jarvis --json url -q .url).git"
git push -u origin main --force

echo ""
echo "=== Done ==="
echo "Your app is live at: $(gh repo view jarvis --json url -q .url)"
echo "Anyone can now clone it and run bash install.sh"
