#!/usr/bin/env bash
# Turns a freshly built Cerium.app into the disk image users download:
#
#   package_dmg.sh path/to/Cerium.app path/to/cerium.dmg
#
# 1. copies the app (the original is left alone) and puts Cerium's icon in
#    it (compile_icon.py, needs Xcode 26);
# 2. adds the bundled extensions (uBlock Origin, Dark Reader) and the
#    initial_preferences that pin them, inside the framework where the browser
#    looks for them (base::DIR_ASSETS); keys come from ../keys (see
#    utils/bundle_extensions.py);
# 3. signs it with sign_app.sh: with a Developer ID and notarized when
#    MACOS_SIGNING_IDENTITY is set, otherwise ad hoc;
# 4. makes a compressed disk image with the app, an Applications shortcut to
#    drag it onto and, unless notarized, a note on how to open it the first
#    time.

set -Eeuo pipefail
# Name the failing command; in GitHub Actions as an annotation, since the
# output of a long step is easy to miss.
trap '_status=$?; printf "%s%s: line %s: %s failed (exit status %s)\n" "${GITHUB_ACTIONS:+::error::}" "${0##*/}" "$LINENO" "$BASH_COMMAND" "$_status" >&2' ERR

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

# The Dock and Finder icon: Chromium's build bundles Chromium's precompiled
# Assets.car, so compile Cerium's (needs Xcode 26) and replace it.
python3 "$_root_dir/compile_icon.py" --output "$_app/Contents/Resources/Assets.car"
cp "$_repo_dir/branding/overlay/chrome/app/theme/chromium/mac/app.icns" "$_app/Contents/Resources/app.icns"

python3 "$_repo_dir/utils/bundle_extensions.py" \
  --output-dir "$_work/extensions" \
  --cache-dir "$_root_dir/build/download_cache" \
  --keys-dir "$_repo_dir/keys"
rm -rf "$_framework/Resources/bundled_extensions"
cp -R "$_work/extensions/bundled_extensions" "$_framework/Resources/"
cp "$_work/extensions/initial_preferences" "$_framework/Resources/"

"$_root_dir/sign_app.sh" "$_app"

ln -s /Applications "$_staging/Applications"
if [[ -z "${MACOS_SIGNING_IDENTITY:-}" ]]; then
  # Not notarized: macOS asks the user to allow the app once.
  cat > "$_staging/How to open $_product.txt" <<EOF
Installing $_product

1. Drag $_product onto the Applications folder.
2. Open $_product from Applications.

$_product is not notarized by Apple, so the first time you open it macOS
refuses ("Apple could not verify ..." or "... can't be opened"). Then:

1. Click Done (or OK).
2. Open System Settings > Privacy & Security.
3. Scroll down to the message about $_product and click Open Anyway.
4. Confirm with your password or Touch ID, and click Open Anyway again.

You only need to do this once. Instead of steps 1-4 you can also run this
in Terminal:

    xattr -dr com.apple.quarantine /Applications/$_product.app

After you install an update, macOS may ask whether $_product may use
"$_product Safe Storage" in your keychain (where $_product keeps the key that
encrypts your saved passwords and cookies). Enter your password and click
Always Allow.

uBlock Origin and Dark Reader are already installed and pinned to the
toolbar.
EOF
fi
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
