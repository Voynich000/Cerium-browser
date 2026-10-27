#!/usr/bin/env bash
# Turns a freshly built Cerium.app into the disk image users download:
#
#   package_dmg.sh path/to/Cerium.app path/to/cerium.dmg
#
# 1. copies the app (the original is left alone);
# 2. adds the bundled extensions (uBlock Origin, Dark Reader) and the
#    initial_preferences that pin them, inside the framework where the browser
#    looks for them (base::DIR_ASSETS); keys come from ../keys (see
#    utils/bundle_extensions.py);
# 3. signs it with sign_app.sh: with a Developer ID and notarized when
#    MACOS_SIGNING_IDENTITY is set, otherwise ad hoc;
# 4. makes a compressed disk image with the app and an Applications shortcut
#    to drag it onto.

set -euo pipefail

_root_dir="$(cd "$(dirname "$0")" && pwd)"
_repo_dir="$(cd "$_root_dir/.." && pwd)"
_source_app="${1:?usage: package_dmg.sh path/to/Cerium.app path/to/output.dmg}"
_target_dmg="${2:?usage: package_dmg.sh path/to/Cerium.app path/to/output.dmg}"
_product="$(basename "$_source_app" .app)"

_work="$(mktemp -d)"
trap 'rm -rf "$_work"' EXIT
_staging="$_work/$_product"
_app="$_staging/$_product.app"
_framework="$_app/Contents/Frameworks/$_product Framework.framework"

mkdir -p "$_staging"
ditto "$_source_app" "$_app"

python3 "$_repo_dir/utils/bundle_extensions.py" \
  --output-dir "$_work/extensions" \
  --cache-dir "$_root_dir/build/download_cache" \
  --keys-dir "$_repo_dir/keys"
rm -rf "$_framework/Resources/bundled_extensions"
cp -R "$_work/extensions/bundled_extensions" "$_framework/Resources/"
cp "$_work/extensions/initial_preferences" "$_framework/Resources/"

"$_root_dir/sign_app.sh" "$_app"

ln -s /Applications "$_staging/Applications"
mkdir -p "$(dirname "$_target_dmg")"
rm -f "$_target_dmg"
# hdiutil occasionally fails with "Resource busy" on CI machines; retry.
for _attempt in 1 2 3 4 5; do
  if hdiutil create -volname "$_product" -srcfolder "$_staging" -fs HFS+ \
      -format UDBZ -ov "$_target_dmg"; then
    break
  fi
  if (( _attempt == 5 )); then
    printf '::error::hdiutil create failed\n' >&2
    exit 1
  fi
  sleep $(( _attempt * 10 ))
done
hdiutil verify "$_target_dmg"
printf 'Packaged %s\n' "$_target_dmg"
