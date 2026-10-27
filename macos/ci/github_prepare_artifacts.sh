#!/bin/bash
# Simple script for packing Cerium macOS build artifacts on GitHub Actions

set -euo pipefail

_target_cpu="${1:-x86_64}"

# The macOS harness lives in macos/ of the Cerium repository.
_root_dir="$(dirname "$(greadlink -f "$0")")"
_src_dir="$_root_dir/build/src"

# If build finished successfully, keep the app for the release workflow
# (.github/workflows/release-macos.yml), which adds the bundled extensions,
# signs it and makes the disk image.
if [[ -f "$_root_dir/build_finished_$_target_cpu.log" ]] ; then
  _chromium_version="$(cat "$_root_dir/ungoogled-chromium/chromium_version.txt")"
  _ungoogled_revision="$(cat "$_root_dir/ungoogled-chromium/revision.txt")"
  _product="$(sed -n 's/^PRODUCT_FULLNAME=//p' "$_src_dir/chrome/app/theme/chromium/BRANDING")"
  _artifact_name="cerium-${_chromium_version}-${_ungoogled_revision}-${_target_cpu}-macos-app"

  mkdir -p app_artifact
  # A tarball keeps the framework's symlinks and file modes, which artifact
  # uploads would lose.
  tar -C "$_src_dir/out/Default" -czf "app_artifact/$_product.app.tar.gz" "$_product.app"
  shasum -a 256 "app_artifact/$_product.app.tar.gz"
  printf 'artifact_name=%s\n' "$_artifact_name" >> "$GITHUB_OUTPUT"
fi

gsync --file-system "$_src_dir"

# Needs to be compressed to stay below GitHub's upload limit 2 GB (?!) 2020-11-24; used to be  5-8GB (?)
tar -C build -c -f - src | zstd -vv -11 -T0 -o build_src.tar.zst

sha256sum ./build_src.tar.zst | tee ./sums.txt

mkdir -p upload_part_build
mv -vn ./*.zst ./sums.txt upload_part_build/ || true
cp -va ./*.log upload_part_build/

ls -kahl upload_part_build/
du -hs upload_part_build/

mkdir upload_logs
mv -vn ./*.log upload_logs/

ls -kahl upload_logs/
du -hs upload_logs/

echo "ready for upload action"
