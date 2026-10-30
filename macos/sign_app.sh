#!/usr/bin/env bash
# Signs Cerium.app (and, with a Developer ID, notarizes it with Apple and
# staples the ticket):
#
#   sign_app.sh path/to/Cerium.app
#
# Without MACOS_SIGNING_IDENTITY the app is signed ad hoc, without the hardened
# runtime and library validation (see adhoc_options): it runs, but macOS
# blocks it on first launch until the user allows it in System Settings.
# With these set it is signed with a Developer ID and notarized, so it opens
# like any other downloaded app:
#
#   MACOS_SIGNING_IDENTITY        "Developer ID Application: ..." certificate
#                                 (name or SHA-1) in the keychain search list
#   MACOS_NOTARIZATION_APPLE_ID   Apple ID of the developer account
#   MACOS_NOTARIZATION_TEAM_ID    its team ID
#   MACOS_NOTARIZATION_PASSWORD   an app-specific password for that Apple ID
#
# Based on ungoogled-chromium-macos's sign_and_package_app.sh.

set -Eeuo pipefail
# Name the failing command; in GitHub Actions as an annotation, since the
# output of a long step is easy to miss.
trap '_status=$?; printf "%s%s: line %s: %s failed (exit status %s)\n" "${GITHUB_ACTIONS:+::error::}" "${0##*/}" "$LINENO" "$BASH_COMMAND" "$_status" >&2' ERR

_root_dir="$(cd "$(dirname "$0")" && pwd)"
_app="${1:?usage: sign_app.sh path/to/Cerium.app}"
_app="$(cd "$(dirname "$_app")" && pwd)/$(basename "$_app")"
_product="$(basename "$_app" .app)"
_framework="$_app/Contents/Frameworks/$_product Framework.framework"
_helpers="$_framework/Helpers"
_libraries="$_framework/Libraries"
_identity="${MACOS_SIGNING_IDENTITY:-}"

# Sign every bundle with its own bundle identifier. If the code signing
# identifier differs from CFBundleIdentifier, macOS privacy permissions such as
# Local Network access do not take effect.
# See https://github.com/ungoogled-software/ungoogled-chromium-macos/issues/361
_bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$_app/Contents/Info.plist")"

# Drops the options that ad hoc signed code cannot use from a --options value
# ("restrict,library,runtime,kill" -> "restrict,kill"). Library validation
# only lets a process load code signed by its own team, and ad hoc signatures
# have no team, so the app could not load its own framework. The hardened
# runtime forces library validation too (CS_FORCED_LV, enforced by macOS 27);
# it is only needed for notarization.
adhoc_options() {
  local value="$1" prefix="" option
  local kept=()
  if [[ "$value" == --options=* ]]; then
    prefix="--options="
    value="${value#--options=}"
  fi
  IFS=',' read -ra _options <<< "$value"
  for option in "${_options[@]}"; do
    [[ "$option" == library || "$option" == runtime ]] || kept+=("$option")
  done
  (IFS=','; printf '%s%s\n' "$prefix" "${kept[*]}")
}

sign() {
  local output arg
  local status=0
  local previous=
  local args=()
  for arg in "$@"; do
    if [[ -z "$_identity" && ( "$previous" == --options || "$arg" == --options=* ) ]]; then
      arg="$(adhoc_options "$arg")"
    fi
    args+=("$arg")
    previous="$arg"
  done
  if [[ -z "$_identity" ]]; then
    output="$(codesign --sign - --force "${args[@]}" 2>&1)" || status=$?
  else
    output="$(codesign --sign "$_identity" --force --timestamp "${args[@]}" 2>&1)" || status=$?
  fi
  [[ -z "$output" ]] || printf '%s\n' "$output"
  if (( status != 0 )); then
    error "codesign failed for ${*: -1}: $output"
    return "$status"
  fi
}

error() {
  printf '::error::%s\n' "$*" >&2
}

warning() {
  printf '::warning::%s\n' "$*" >&2
}

# Clear extended attributes from every file: codesign refuses files with
# some of them (e.g. com.apple.FinderInfo), and a quarantined app makes macOS
# ask for permission for incoming network connections
# (https://github.com/ungoogled-software/ungoogled-chromium-macos/issues/17).
xattr -cr "$_app"

# Sign the binary
sign --identifier chrome_crashpad_handler --options=restrict,library,runtime,kill "$_helpers/chrome_crashpad_handler"
sign --identifier "$_bundle_id.helper" --options restrict,library,runtime,kill "$_helpers/$_product Helper.app"
sign --identifier "$_bundle_id.helper.renderer" --options restrict,kill,runtime --entitlements "$_root_dir/entitlements/helper-renderer-entitlements.plist" "$_helpers/$_product Helper (Renderer).app"
sign --identifier "$_bundle_id.helper" --options restrict,kill,runtime --entitlements "$_root_dir/entitlements/helper-gpu-entitlements.plist" "$_helpers/$_product Helper (GPU).app"
sign --identifier "$_bundle_id.framework.AlertNotificationService" --options restrict,library,runtime,kill "$_helpers/$_product Helper (Alerts).app"
if [[ -d "$_helpers/$_product Helper (Aperitif).app" ]]; then
  sign --identifier "$_bundle_id.helper" --options restrict,library,runtime,kill "$_helpers/$_product Helper (Aperitif).app"
  sign --identifier "$_bundle_id.helper.renderer" --options restrict,kill,runtime --entitlements "$_root_dir/entitlements/helper-renderer-entitlements.plist" "$_helpers/$_product Helper (Aperitif Renderer).app"
  sign --identifier "$_bundle_id.helper" --options restrict,kill,runtime --entitlements "$_root_dir/entitlements/helper-gpu-entitlements.plist" "$_helpers/$_product Helper (Aperitif GPU).app"
  sign --identifier "$_bundle_id.framework.AlertNotificationService" --options restrict,library,runtime,kill "$_helpers/$_product Helper (Aperitif Alerts).app"
fi
sign --identifier app_mode_loader --options restrict,library,runtime,kill "$_helpers/app_mode_loader"
sign --identifier web_app_shortcut_copier --options restrict,library,runtime,kill "$_helpers/web_app_shortcut_copier"

# The dylibs bundled by Chromium vary by release. Sign the files that are
# actually present instead of maintaining a version-dependent hard-coded list.
for _dylib in "$_libraries"/*.dylib; do
  [[ -e "$_dylib" ]] || continue
  _dylib_name="$(basename "$_dylib" .dylib)"
  sign --identifier "$_dylib_name" "$_dylib"
done

sign --identifier "$_bundle_id.framework" "$_framework"
if [[ -z "$_identity" ]]; then
  sign --identifier "$_bundle_id" --options restrict,library,runtime,kill --entitlements "$_root_dir/entitlements/app-entitlements.plist" "$_app"
else
  sign --identifier "$_bundle_id" --options restrict,library,runtime,kill --entitlements "$_root_dir/entitlements/app-entitlements.plist" --requirements "=designated => identifier \"$_bundle_id\" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */" "$_app"
fi

# Verify the binary signature
verify_dylib() {
  local dylib="$1"
  local signature_info

  codesign --verify --strict --verbose=4 "$dylib"

  if [[ -z "$_identity" ]]; then
    return
  fi

  signature_info="$(codesign -dvvv "$dylib" 2>&1)"
  printf '%s\n' "$signature_info"

  if ! grep -q '^Authority=Developer ID Application:' <<< "$signature_info"; then
    error "${dylib} is not signed with a Developer ID Application certificate"
    return 1
  fi

  if ! grep -q '^Timestamp=' <<< "$signature_info"; then
    error "${dylib} does not have a secure signing timestamp"
    return 1
  fi
}

# Check all framework dylibs to catch signing omissions in future releases.
for _framework_dylib in "$_libraries"/*.dylib; do
  verify_dylib "$_framework_dylib"
done

codesign --verify --deep --strict --verbose=4 "$_app"

# Check that every bundle is signed with its own bundle identifier
verify_identifier() {
  local bundle="$1"
  local info_plist="$2"
  local bundle_id signing_id

  bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$info_plist")"
  signing_id="$(codesign -dv "$bundle" 2>&1 | sed -n 's/^Identifier=//p')"

  if [[ "$signing_id" != "$bundle_id" ]]; then
    error "${bundle} is signed as ${signing_id} but its bundle identifier is ${bundle_id}"
    return 1
  fi
}

for _bundle in "$_app" "$_helpers"/*.app; do
  verify_identifier "$_bundle" "$_bundle/Contents/Info.plist"
done
verify_identifier "$_framework" "$_framework/Resources/Info.plist"

notarize_app() {
  local work result log status submission_id
  local submit_exit=0
  local credentials=(
    --apple-id "${MACOS_NOTARIZATION_APPLE_ID:?MACOS_NOTARIZATION_APPLE_ID is not set}"
    --team-id "${MACOS_NOTARIZATION_TEAM_ID:?MACOS_NOTARIZATION_TEAM_ID is not set}"
    --password "${MACOS_NOTARIZATION_PASSWORD:?MACOS_NOTARIZATION_PASSWORD is not set}"
  )
  work="$(mktemp -d)"
  result="$work/notary-result.json"
  log="$work/notary-log.json"

  ditto -c -k --keepParent "$_app" "$work/notarize.zip"

  xcrun notarytool submit --wait --output-format json "${credentials[@]}" "$work/notarize.zip" \
    > "$result" || submit_exit=$?

  cat "$result"

  status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", ""))' "$result" 2>/dev/null)" || status=""
  submission_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$result" 2>/dev/null)" || submission_id=""

  if [[ -n "$submission_id" ]]; then
    if xcrun notarytool log "${credentials[@]}" "$submission_id" > "$log"; then
      cat "$log"
    else
      warning "Could not retrieve Apple's notarization log for submission $submission_id"
      [[ ! -s "$log" ]] || cat "$log" || true
    fi
  fi

  if [[ "$status" != "Accepted" ]]; then
    if [[ -n "$status" ]]; then
      error "Apple notarization failed with status: $status"
    elif (( submit_exit != 0 )); then
      error "notarytool submit failed with exit status $submit_exit"
    else
      error "Apple notarization returned no status"
    fi
    return 1
  fi

  if (( submit_exit != 0 )); then
    error "notarytool submit exited with status $submit_exit despite reporting Accepted"
    return "$submit_exit"
  fi

  if [[ -z "$submission_id" ]]; then
    error "Apple notarization returned Accepted without a submission ID"
    return 1
  fi

  rm -rf "$work"
  xcrun stapler staple "$_app"
  xcrun stapler validate "$_app"
  # The check Gatekeeper makes when a downloaded app is opened.
  spctl --assess --type execute --verbose=4 "$_app"
}

if [[ -z "$_identity" ]]; then
  printf 'Signed ad hoc; skipping notarization.\n'
else
  notarize_app
fi
