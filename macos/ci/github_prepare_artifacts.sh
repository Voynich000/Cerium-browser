#!/bin/bash
# Simple script for packing Ungoogled-Chromium macOS build artifacts on GitHub Actions

set -euo pipefail

_target_cpu="${1:-x86_64}"

# The macOS harness lives in macos/ of the Cerium repository.
_root_dir="$(dirname "$(greadlink -f "$0")")"
_repo_dir="$(greadlink -f "$_root_dir/..")"
_src_dir="$_root_dir/build/src"

# If build finished successfully
if [[ -f "$_root_dir/build_finished_$_target_cpu.log" ]] ; then
  # For packaging
  _chromium_version="$(cat "$_root_dir/ungoogled-chromium/chromium_version.txt")"
  _ungoogled_revision="$(cat "$_root_dir/ungoogled-chromium/revision.txt")"

  _file_name="cerium-${_chromium_version}-${_ungoogled_revision}-${_target_cpu}-macos.dmg"
  _hash_name="${_file_name}.hashes.md"

  # Sign the bundled extensions with the keys from the repository secrets
  # (new keys, and so new extension IDs, if they are not set).
  mkdir -p "$_repo_dir/keys"
  chmod 700 "$_repo_dir/keys"
  if [[ -n "${CERIUM_UBLOCK_ORIGIN_KEY:-}" ]]; then
    printf '%s\n' "$CERIUM_UBLOCK_ORIGIN_KEY" > "$_repo_dir/keys/ublock_origin.pem"
  fi
  if [[ -n "${CERIUM_DARK_READER_KEY:-}" ]]; then
    printf '%s\n' "$CERIUM_DARK_READER_KEY" > "$_repo_dir/keys/dark_reader.pem"
  fi
  if [[ ! -f "$_repo_dir/keys/ublock_origin.pem" || ! -f "$_repo_dir/keys/dark_reader.pem" ]]; then
    echo "::warning::Extension signing key secrets are not set; this build gets new extension IDs."
  fi
  python3 "$_repo_dir/utils/bundle_extensions.py" \
    --output-dir "$_src_dir/out/Default" \
    --cache-dir "$_root_dir/build/download_cache" \
    --keys-dir "$_repo_dir/keys"

  cd "$_src_dir"

  # Cerium has no Apple Developer ID: sign ad hoc and skip notarization.
  MACOS_AD_HOC_SIGNING=1 "$_root_dir/sign_and_package_app.sh" "$_root_dir/$_file_name"

  cd "$_root_dir"
  echo -e "md5: \nsha1: \nsha256: " | tee ./hash_types.txt
  { md5sum "$_file_name" ; sha1sum "$_file_name" ; sha256sum "$_file_name" ; } | tee ./sums.txt

  _hash_md=$(paste ./hash_types.txt ./sums.txt | awk '{print $1 " " $2}')

  printf 'file_name=%s\n' "$_file_name" >> "$GITHUB_OUTPUT"

  printf '[Hashes](https://en.wikipedia.org/wiki/Cryptographic_hash_function) for the disk image `%s`: \n' "$_file_name" | tee -a ./${_hash_name}
  printf '\n```\n%s\n```\n' "$_hash_md" | tee -a ./${_hash_name}

  # Use separate folder for build product, so that it can be used as individual asset in case the release action fails
  mkdir -p release_asset
  mv -vn ./*.dmg release_asset/ || true

  ls -kahl release_asset/
  du -hs release_asset/
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
