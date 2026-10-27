#!/bin/bash
set -euo pipefail

_current_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
_root_dir="$(cd "$_current_dir/.." && pwd)"
_build_dir="$_root_dir/build"
_release_dir="$_build_dir/release"
_app_dir="$_release_dir/cerium.AppDir"

_chromium_version=$(cat "$_root_dir/ungoogled-chromium/chromium_version.txt")
_ungoogled_revision=$(cat "$_root_dir/ungoogled-chromium/revision.txt")

_app_name="cerium"
_version="$_chromium_version-$_ungoogled_revision"

_arch=$(cat "$_build_dir/src/out/Default/args.gn" \
                | grep ^target_cpu \
                | tail -1 \
                | sed 's/.*=//' \
                | cut -d'"' -f2)

if [ "$_arch" = "x64" ]; then
    _arch="x86_64"
fi

_release_name="$_app_name-$_version-$_arch"
# AppImage self-update information, only when building on GitHub Actions
_update_args=()
if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    _update_args=(-u "gh-releases-zsync|${GITHUB_REPOSITORY%%/*}|${GITHUB_REPOSITORY##*/}|latest|$_app_name-*-$_arch.AppImage.zsync")
fi
_tarball_name="${_release_name}_linux"
_tarball_dir="$_release_dir/$_tarball_name"

_files="bundled_extensions/
chrome
chrome_100_percent.pak
chrome_200_percent.pak
chrome_crashpad_handler
chromedriver
chrome-wrapper
icudtl.dat
initial_preferences
libEGL.so
libGLESv2.so
libqt5_shim.so
libqt6_shim.so
libvk_swiftshader.so
libvulkan.so.1
locales/
product_logo_48.png
resources.pak
v8_context_snapshot.bin
vk_swiftshader_icd.json
xdg-mime
xdg-settings"

echo "copying release files and creating $_tarball_name.tar.xz"

mkdir -p "$_tarball_dir"

for file in $_files; do
    cp -r "$_build_dir/src/out/Default/$file" "$_tarball_dir" &
done
wait

_size="$(du -sk "$_tarball_dir" | cut -f1)"

pushd "$_release_dir"

tar vcf - "$_tarball_name" \
    | pv -s"${_size}k" \
    | xz -e9 > "$_release_dir/$_tarball_name.tar.xz" &

# create AppImage
rm -rf "$_app_dir"
mkdir -p "$_app_dir/opt/cerium/" "$_app_dir/usr/share/icons/hicolor/48x48/apps/"
cp -r "$_tarball_dir"/* "$_app_dir/opt/cerium/"
cp "$_root_dir/package/cerium.desktop" "$_app_dir"
sed -i -e 's|Exec=cerium|Exec=AppRun|g' "$_app_dir/cerium.desktop"

cat > "$_app_dir/AppRun" <<'EOF'
#!/bin/sh
THIS="$(readlink -f "${0}")"
HERE="$(dirname "${THIS}")"
export LD_LIBRARY_PATH="${HERE}"/usr/lib:$PATH
export CHROME_WRAPPER="${THIS}"
"${HERE}"/opt/cerium/chrome "$@"
EOF
chmod a+x "$_app_dir/AppRun"

cp "${_app_dir}/opt/cerium/product_logo_48.png" "$_app_dir/usr/share/icons/hicolor/48x48/apps/cerium.png"
cp "${_app_dir}/usr/share/icons/hicolor/48x48/apps/cerium.png" "$_app_dir"

export APPIMAGETOOL_APP_NAME="$_app_name"
export VERSION="$_version"

appimagetool \
    "${_update_args[@]}" \
    "$_app_dir" \
    "$_release_name.AppImage" &
popd
wait

rm -rf "$_tarball_dir" "$_app_dir"
