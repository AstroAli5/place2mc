#!/bin/bash
# place2mc: double-click this file to start the app (the first time, right-click > Open).
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python is not installed. Get it from https://www.python.org/downloads/ then open this file again."
  open "https://www.python.org/downloads/"
  read -p "Press Enter to close."
  exit 1
fi
echo "Checking the two things place2mc needs (numpy and pillow)..."
python3 -m pip install --user --quiet --disable-pip-version-check numpy pillow
python3 place2mc_app.py
