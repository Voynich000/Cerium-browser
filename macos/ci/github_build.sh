#!/bin/bash -eux

# Simple partitioned (2 of 3) build script for building Ungoogled-Chromium macOS binaries on GitHub Actions
# Resuming build script for macOS

_target_cpu="${1:-x86_64}"

_root_dir="$(dirname "$(greadlink -f "$0")")"
_src_dir="$_root_dir/build/src"
if [[ -f "$_root_dir/epoch_job_start.txt" ]]; then
  epoch_job_start=$(cat "$_root_dir/epoch_job_start.txt")
fi

# GitHub's hard time limit is 6 h per job, we want to spare 1 h for steps before and after the build,
# To get the remaining time for building we subtract 360*60s - 60*60s - (epoch_now - epoch_job_start)
remaining_time() {
  if [[ -n "${epoch_job_start:-}" ]]; then
    echo $(( 360*60 - 60*60 - $(date +%s) + epoch_job_start ))
  else
    echo 19680  # 328 m
  fi
}

# Cerium: runs ninja on the given targets within the remaining time; when it
# fails, reports the failures as annotations (see report_build_errors.py).
run_ninja() {
  local status
  timeout -k 7m -s SIGTERM "$(remaining_time)s" ninja -C out/Default "$@" 2>&1 | tee "$_root_dir/ninja_output.log"
  status="${PIPESTATUS[0]}"
  if [[ "$status" -ne 0 && "$status" -ne 124 ]]; then
    python3 "$_root_dir/report_build_errors.py" "$_root_dir/ninja_output.log"
  fi
  return "$status"
}

cd "$_src_dir"

# The JavaScript half of esbuild, for DevTools' build scripts (the binary is
# Homebrew's; see third_party/devtools-frontend/src/scripts/build/esbuild.js).
# It is installed outside the source tree, and only the source tree is handed
# from one job to the next, so every job installs it, matching its Homebrew
# esbuild. (Cerium: upstream does this once, in github_before_build.sh.)
_esbuild_version="$(/opt/homebrew/bin/esbuild --version)"
_esbuild_module="$_root_dir/build/esbuild-node/node_modules/esbuild"
if [[ ! -f "$_esbuild_module/package.json" ]] || \
    [[ "$(node -p 'require(process.argv[1]).version' "$_esbuild_module/package.json")" != "$_esbuild_version" ]]; then
  npm install --prefix "$_root_dir/build/esbuild-node" --no-save --package-lock=false --no-bin-links --ignore-scripts --omit=optional --no-audit --no-fund "esbuild@$_esbuild_version"
fi
rm -rf third_party/devtools-frontend/src/node_modules/esbuild
ln -s "$_esbuild_module" third_party/devtools-frontend/src/node_modules/esbuild

echo $(date +%s) | tee -a "$_root_dir/build_times_$_target_cpu.log"
echo "status=running" >> $GITHUB_OUTPUT

set +e

# Cerium: compile the files Cerium's patches change first, so that a mistake
# in them stops the first build job instead of hours into the build. Once
# built, later jobs find them up to date.
_cerium_targets="$(python3 "$_root_dir/cerium_first_targets.py" "$_src_dir")"
if [[ -n "$_cerium_targets" ]]; then
  # shellcheck disable=SC2086
  run_ninja $_cerium_targets
  _error_code="${?}"
  if [[ "$_error_code" -eq 124 ]]; then
      exit 0
  fi
  if [[ "$_error_code" -ne 0 ]]; then
      exit "$_error_code"
  fi
  echo "::notice::Cerium's changes compiled: $(wc -w <<< "$_cerium_targets") targets"
else
  echo "::warning::Found no targets for Cerium's changes; they are compiled with the rest"
fi

run_ninja chrome chromedriver

_error_code="${?}"
if [[ "$_error_code" -eq 124 ]]; then
    exit 0
fi

if [[ "$_error_code" -ne 0 ]]; then
    exit "$_error_code"
fi

set -e

echo $(date +%s) | tee "$_root_dir/build_finished_$_target_cpu.log"
echo "status=finished" >> $GITHUB_OUTPUT
