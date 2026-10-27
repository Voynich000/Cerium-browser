#!/usr/bin/env bash

set -euo pipefail
shopt -s extglob

# Use the newest Xcode 26 on the runner (GitHub's macOS images carry several).
BASE_XCODE_PATH="$(ls -d /Applications/Xcode_26*.app 2>/dev/null | grep -v '^/Applications/Xcode_26.app$' | sort -V | tail -1 || true)"
if [ -z "$BASE_XCODE_PATH" ] && [ -e /Applications/Xcode_26.app ]; then
  BASE_XCODE_PATH=/Applications/Xcode_26.app
fi

if [ -z "$BASE_XCODE_PATH" ] || [ ! -e "$BASE_XCODE_PATH" ]; then
  echo "Failed to find a suitable version of Xcode"
  ls -l /Applications | grep Xcode || true
  exit 1
fi

TARGET_XCODE_VERSION="$(readlink -f "$BASE_XCODE_PATH" | xargs basename | sed 's/Xcode_//' | sed 's/.app//')"

ls -l /Applications | grep Xcode

sudo rm -rf /Applications/Xcode_!("$TARGET_XCODE_VERSION").app
if [ ! -e /Applications/Xcode_26.app ]; then
  # try to keep paths consistent between jobs.
  sudo mv "/Applications/Xcode_$TARGET_XCODE_VERSION.app" /Applications/Xcode_26.app
fi

sudo xcode-select --switch /Applications/Xcode_26.app
sudo xcrun simctl delete all
