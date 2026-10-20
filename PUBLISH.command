#!/bin/bash
# Double-click this to publish JARVIS to GitHub.
# It runs publish.sh and keeps the window open so you can see what happened.
cd "$(dirname "$0")"
bash publish.sh
echo ""
echo "Press any key to close."
read -r -n 1
