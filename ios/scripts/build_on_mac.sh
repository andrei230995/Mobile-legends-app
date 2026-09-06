#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v xcodebuild >/dev/null 2>&1; then
  echo 'Full Xcode is required on a Mac. This environment cannot build an iPhone app.' >&2
  exit 1
fi
python3 "$project_root/scripts/prepare_assets.py"
xcodebuild -project "$project_root/Rankwise.xcodeproj" -scheme Rankwise -configuration Debug -destination 'generic/platform=iOS Simulator' -derivedDataPath "$project_root/.build" CODE_SIGNING_ALLOWED=NO build
