#!/bin/bash
set -euo pipefail

# Downloads the Chromium source and runs the source preparation of
# scripts/build.sh (binary pruning, ungoogled-chromium's patches, patches/series,
# domain substitution and renaming to Cerium) without building anything.
# Needs python3, GNU patch and about 20 GB of disk space.
# Set CERIUM_PATCHES_DIR=macos/patches to check the macOS patch series instead.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)/shared.sh"

setup_paths
fetch_sources false false
apply_patches
apply_domsub
apply_rebrand

echo "All patches, domain substitution and renaming applied to ${_src_dir}"
