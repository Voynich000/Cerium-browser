#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2019 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Cerium build script for Microsoft Windows (x64)

Cerium's copy of ungoogled-chromium-windows's build.py (156.0.8078.47-1.1). The
harness lives in windows/ of the Cerium repository and uses its
ungoogled-chromium submodule. Cerium adds: the Cerium patches (via
windows/patches/series), utils/rebrand.py after domain substitution, the
bundled extensions in the build output (for the installer and the .zip), its
own changes compiled first, and build errors and progress as GitHub Actions
annotations.
"""

import sys
import time
import argparse
import os
import re
import shutil
import subprocess
import ctypes
import importlib.util
import threading
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parent
_REPO_DIR = _ROOT_DIR.parent
_UNGOOGLED_DIR = _REPO_DIR / 'ungoogled-chromium'

sys.path.insert(0, str(_UNGOOGLED_DIR / 'utils'))
import downloads
import domain_substitution
import prune_binaries
import patches
from _common import ENCODING, USE_REGISTRY, ExtractorEnum, get_logger
sys.path.pop(0)
sys.path.insert(0, str(_REPO_DIR / 'utils'))
import cerium_first_targets
import report_build_errors
sys.path.pop(0)

_PATCH_BIN_RELPATH = Path('third_party/git/usr/bin/patch.exe')
_BUILD_TIMEOUT_EXIT_CODE = 124


class _BuildTimeout(Exception):
    """Raised when the CI build time limit is reached."""


def _get_vcvars_path(name='64'):
    """
    Returns the path to the corresponding vcvars*.bat path

    As of VS 2017, name can be one of: 32, 64, all, amd64_x86, x86_amd64
    """
    vswhere_exe = '%ProgramFiles(x86)%\\Microsoft Visual Studio\\Installer\\vswhere.exe'
    result = subprocess.run(
        '"{}" -products * -prerelease -latest -property installationPath'.format(vswhere_exe),
        shell=True,
        check=True,
        stdout=subprocess.PIPE,
        universal_newlines=True)
    vcvars_path = Path(result.stdout.strip(), 'VC/Auxiliary/Build/vcvars{}.bat'.format(name))
    if not vcvars_path.exists():
        raise RuntimeError(
            'Could not find vcvars batch script in expected location: {}'.format(vcvars_path))
    return vcvars_path


def _run_build_process(*args, **kwargs):
    """
    Runs the subprocess with the correct environment variables for building
    """
    # Add call to set VC variables
    cmd_input = ['call "%s" >nul' % _get_vcvars_path()]
    cmd_input.append('set DEPOT_TOOLS_WIN_TOOLCHAIN=0')
    cmd_input.append(' '.join(map('"{}"'.format, args)))
    cmd_input.append('exit\n')
    subprocess.run(('cmd.exe', '/k'),
                   input='\n'.join(cmd_input),
                   check=True,
                   encoding=ENCODING,
                   **kwargs)


def _run_build_process_output(*args):
    """
    Runs the subprocess with the build environment and returns its output
    """
    command = 'call "{}" >nul && set DEPOT_TOOLS_WIN_TOOLCHAIN=0&& {}'.format(
        _get_vcvars_path(), ' '.join(map('"{}"'.format, args)))
    return subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE,
                          encoding=ENCODING).stdout


def _run_build_process_timeout(*args, timeout, log_path=None):
    """
    Runs the subprocess with the correct environment variables for building

    Cerium: its output also goes to log_path, for report_build_errors.
    """
    # Add call to set VC variables
    cmd_input = ['call "%s" >nul' % _get_vcvars_path()]
    cmd_input.append('set DEPOT_TOOLS_WIN_TOOLCHAIN=0')
    cmd_input.append(' '.join(map('"{}"'.format, args)))
    cmd_input.append('exit\n')
    with subprocess.Popen(('cmd.exe', '/k'), encoding=ENCODING, errors='replace',
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,
                          creationflags=subprocess.CREATE_NEW_PROCESS_GROUP) as proc:
        log = open(log_path, 'w', encoding=ENCODING, errors='replace') if log_path else None

        def copy_output():
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                if log:
                    log.write(line)

        copier = threading.Thread(target=copy_output, daemon=True)
        copier.start()
        proc.stdin.write('\n'.join(cmd_input))
        proc.stdin.close()
        try:
            proc.wait(timeout)
            copier.join(60)
            if log:
                log.close()
            if proc.returncode != 0:
                raise RuntimeError('Build failed!')
        except subprocess.TimeoutExpired:
            print('Sending keyboard interrupt')
            for _ in range(3):
                ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, proc.pid)
                time.sleep(1)
            try:
                proc.wait(10)
            except:
                proc.kill()
            copier.join(60)
            if log:
                log.close()
            raise _BuildTimeout


def _bundle_extensions(build_outputs, downloads_cache):
    """
    Cerium: packs uBlock Origin and Dark Reader into the build outputs, where
    the installer (chrome.release) and package.py pick them up
    """
    if (build_outputs / 'bundled_extensions').exists() and \
            (build_outputs / 'initial_preferences').exists():
        return
    keys_dir = _REPO_DIR / 'keys'
    keys_dir.mkdir(exist_ok=True)
    for name, variable in (('ublock_origin', 'CERIUM_UBLOCK_ORIGIN_KEY'),
                           ('dark_reader', 'CERIUM_DARK_READER_KEY')):
        if os.environ.get(variable):
            (keys_dir / f'{name}.pem').write_text(os.environ[variable].strip() + '\n',
                                                 encoding=ENCODING)
    if not all((keys_dir / f'{name}.pem').exists() for name in ('ublock_origin', 'dark_reader')):
        print('::warning::Extension signing key secrets are not set; '
              'this build gets new extension IDs.')
    subprocess.run([sys.executable, str(_REPO_DIR / 'utils' / 'bundle_extensions.py'),
                    '--output-dir', str(build_outputs), '--cache-dir', str(downloads_cache),
                    '--keys-dir', str(keys_dir)], check=True)


def _make_tmp_paths():
    """Creates TMP and TEMP variable dirs so ninja won't fail"""
    tmp_path = Path(os.environ['TMP'])
    if not tmp_path.exists():
        tmp_path.mkdir()
    tmp_path = Path(os.environ['TEMP'])
    if not tmp_path.exists():
        tmp_path.mkdir()


def main():
    """CLI Entrypoint"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--disable-ssl-verification',
        action='store_true',
        help='Disables SSL verification for downloading')
    parser.add_argument(
        '--7z-path',
        dest='sevenz_path',
        default=USE_REGISTRY,
        help=('Command or path to 7-Zip\'s "7z" binary. If "_use_registry" is '
              'specified, determine the path from the registry. Default: %(default)s'))
    parser.add_argument(
        '--winrar-path',
        dest='winrar_path',
        default=USE_REGISTRY,
        help=('Command or path to WinRAR\'s "winrar.exe" binary. If "_use_registry" is '
              'specified, determine the path from the registry. Default: %(default)s'))
    parser.add_argument(
        '-j',
        type=int,
        dest='thread_count',
        help=('Number of CPU threads to use for compiling'))
    parser.add_argument(
        '--ci',
        action='store_true'
    )
    parser.add_argument(
        '--x86',
        action='store_true'
    )
    parser.add_argument(
        '--arm',
        action='store_true'
    )
    parser.add_argument(
        '--tarball',
        action='store_true'
    )
    args = parser.parse_args()

    # Set common variables
    source_tree = _ROOT_DIR / 'build' / 'src'
    downloads_cache = _ROOT_DIR / 'build' / 'download_cache'

    if not args.ci or not (source_tree / 'BUILD.gn').exists():
        # Setup environment
        source_tree.mkdir(parents=True, exist_ok=True)
        downloads_cache.mkdir(parents=True, exist_ok=True)
        _make_tmp_paths()

        # Extractors
        extractors = {
            ExtractorEnum.SEVENZIP: args.sevenz_path,
            ExtractorEnum.WINRAR: args.winrar_path,
        }

        # Prepare source folder
        if args.tarball:
            # Download chromium tarball
            get_logger().info('Downloading chromium tarball...')
            download_info = downloads.DownloadInfo([_UNGOOGLED_DIR / 'downloads.ini'])
            downloads.retrieve_downloads(download_info, downloads_cache, None, True, args.disable_ssl_verification)
            try:
                downloads.check_downloads(download_info, downloads_cache, None)
            except downloads.HashMismatchError as exc:
                get_logger().error('File checksum does not match: %s', exc)
                exit(1)

            # Unpack chromium tarball
            get_logger().info('Unpacking chromium tarball...')
            downloads.unpack_downloads(download_info, downloads_cache, None, source_tree, extractors)
        else:
            # Clone sources
            subprocess.run([sys.executable, str(_UNGOOGLED_DIR / 'utils' / 'clone.py'), '-o', str(source_tree), '-p', 'win32' if args.x86 else 'win-arm64' if args.arm else 'win64'], check=True)

        # Retrieve windows downloads
        get_logger().info('Downloading required files...')
        download_info_win = downloads.DownloadInfo([_ROOT_DIR / 'downloads.ini'])
        downloads.retrieve_downloads(download_info_win, downloads_cache, None, True, args.disable_ssl_verification)
        try:
            downloads.check_downloads(download_info_win, downloads_cache, None)
        except downloads.HashMismatchError as exc:
            get_logger().error('File checksum does not match: %s', exc)
            exit(1)

        # Prune binaries
        pruning_list = (_UNGOOGLED_DIR / 'pruning.list') if args.tarball else (_ROOT_DIR  / 'pruning.list')
        unremovable_files = prune_binaries.prune_files(
            source_tree,
            pruning_list.read_text(encoding=ENCODING).splitlines()
        )
        if unremovable_files:
            get_logger().error('Files could not be pruned: %s', unremovable_files)
            parser.exit(1)

        # Unpack downloads
        DIRECTX = source_tree / 'third_party' / 'microsoft_dxheaders' / 'src'
        ESBUILD = source_tree / 'third_party' / 'devtools-frontend' / 'src' / 'third_party' / 'esbuild'
        if DIRECTX.exists():
            shutil.rmtree(DIRECTX)
            DIRECTX.mkdir()
        if ESBUILD.exists():
            shutil.rmtree(ESBUILD)
            ESBUILD.mkdir()
        get_logger().info('Unpacking downloads...')
        downloads.unpack_downloads(download_info_win, downloads_cache, None, source_tree, extractors)

        # Apply patches
        # First, ungoogled-chromium-patches
        patches.apply_patches(
            patches.generate_patches_from_series(_UNGOOGLED_DIR / 'patches', resolve=True),
            source_tree,
            patch_bin_path=(source_tree / _PATCH_BIN_RELPATH)
        )
        # Then Windows-specific patches, and Cerium's (see patches/series)
        patches.apply_patches(
            patches.generate_patches_from_series(_ROOT_DIR / 'patches', resolve=True),
            source_tree,
            patch_bin_path=(source_tree / _PATCH_BIN_RELPATH)
        )

        # Substitute domains
        domain_substitution_list = (_UNGOOGLED_DIR / 'domain_substitution.list') if args.tarball else (_ROOT_DIR  / 'domain_substitution.list')
        domain_substitution.apply_substitution(
            _UNGOOGLED_DIR / 'domain_regex.list',
            domain_substitution_list,
            source_tree,
            None
        )

        # Cerium: rename the browser and swap in its logos and icons
        subprocess.run([sys.executable, str(_REPO_DIR / 'utils' / 'rebrand.py'), str(source_tree)],
                       check=True)

    # Check if rust-toolchain folder has been populated
    HOST_CPU_IS_64BIT = sys.maxsize > 2**32
    RUST_DIR_DST = source_tree / 'third_party' / 'rust-toolchain'
    RUST_DIR_SRC64 = source_tree / 'third_party' / 'rust-toolchain-x64'
    RUST_DIR_SRC86 = source_tree / 'third_party' / 'rust-toolchain-x86'
    RUST_DIR_SRCARM = source_tree / 'third_party' / 'rust-toolchain-arm'
    RUST_FLAG_FILE = RUST_DIR_DST / 'INSTALLED_VERSION'
    if not args.ci or not RUST_FLAG_FILE.exists():
        # Directories to copy from source to target folder
        DIRS_TO_COPY = ['bin', 'lib']

        # Loop over all source folders
        for rust_dir_src in [RUST_DIR_SRC64, RUST_DIR_SRC86, RUST_DIR_SRCARM]:
            # Loop over all dirs to copy
            for dir_to_copy in DIRS_TO_COPY:
                # Copy bin folder for host architecture
                if (dir_to_copy == 'bin') and (HOST_CPU_IS_64BIT != (rust_dir_src == RUST_DIR_SRC64)):
                    continue

                # Create target dir
                target_dir = RUST_DIR_DST / dir_to_copy
                if not os.path.isdir(target_dir):
                    os.makedirs(target_dir)

                # Loop over all subfolders of the rust source dir
                for cp_src in rust_dir_src.glob(f'*/{dir_to_copy}/*'):
                    cp_dst = target_dir / cp_src.name
                    if cp_src.is_dir():
                        shutil.copytree(cp_src, cp_dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(cp_src, cp_dst)

        # Generate version file
        with open(RUST_FLAG_FILE, 'w') as f:
            subprocess.run([source_tree / 'third_party' / 'rust-toolchain-x64' / 'rustc' / 'bin' / 'rustc.exe', '--version'], stdout=f)

    if not args.ci or not (source_tree / 'out/Default').exists():
        # Output args.gn
        (source_tree / 'out/Default').mkdir(parents=True)
        gn_flags = (_UNGOOGLED_DIR / 'flags.gn').read_text(encoding=ENCODING)
        gn_flags += '\n'
        windows_flags = (_ROOT_DIR / 'flags.windows.gn').read_text(encoding=ENCODING)
        if args.x86:
            windows_flags = windows_flags.replace('x64', 'x86')
            windows_flags += '\nv8_enable_drumbrake=false\n'
        elif args.arm:
            windows_flags = windows_flags.replace('x64', 'arm64')
        if args.tarball:
            windows_flags += '\nchrome_pgo_phase=0\n'
        # Point V8 metagen at the local libclang and Python cindex bindings
        clang_spec = importlib.util.find_spec('clang')
        if clang_spec is None or not clang_spec.submodule_search_locations:
            get_logger().error('Python clang bindings not found. Install them with: pip install clang')
            sys.exit(1)
        libclang_so = source_tree / 'third_party' / 'llvm-build' / 'Release+Asserts' / 'bin' / 'libclang.dll'
        libclang_bindings_dir = Path(list(clang_spec.submodule_search_locations)[0]).parent
        windows_flags += f'\nv8_metagen_libclang_so="{libclang_so.resolve().as_posix()}"\n'
        windows_flags += f'v8_metagen_libclang_bindings_dir="{libclang_bindings_dir.resolve().as_posix()}"\n'
        gn_flags += windows_flags
        (source_tree / 'out/Default/args.gn').write_text(gn_flags, encoding=ENCODING)

    # Enter source tree to run build commands
    os.chdir(source_tree)

    if not args.ci or not os.path.exists('out\\Default\\gn.exe'):
        # Run GN bootstrap
        _run_build_process(
            sys.executable, 'tools\\gn\\bootstrap\\bootstrap.py', '-o', 'out\\Default\\gn.exe',
            '--skip-generate-buildfiles')

        # Run gn gen
        _run_build_process('out\\Default\\gn.exe', 'gen', 'out\\Default', '--fail-on-unused-args')

    if not args.ci or not os.path.exists('third_party\\rust-toolchain\\bin\\bindgen.exe'):
        # Build bindgen
        _run_build_process(
            sys.executable,
            'tools\\rust\\build_bindgen.py', '--skip-test')

    # Ninja commandline
    ninja_commandline = ['third_party\\ninja\\ninja.exe']
    if args.thread_count is not None:
        ninja_commandline.append('-j')
        ninja_commandline.append(str(args.thread_count))
    ninja_commandline.append('-C')
    ninja_commandline.append('out\\Default')
    targets = ['chrome', 'chromedriver', 'mini_installer']

    # Run ninja
    if args.ci:
        log_path = _ROOT_DIR / 'build' / 'ninja_output.log'
        deadline = time.monotonic() + 3.5 * 60 * 60

        def run_ninja(*ninja_targets):
            remaining = deadline - time.monotonic()
            if remaining < 60:
                raise _BuildTimeout
            try:
                _run_build_process_timeout(*ninja_commandline, *ninja_targets,
                                           timeout=remaining, log_path=log_path)
            except RuntimeError:
                report_build_errors.report(log_path)
                raise
            except _BuildTimeout:
                report_build_errors.report_progress(log_path)
                raise

        # Cerium: compile the files Cerium's patches change first, so that a
        # mistake in them stops the first build job instead of hours into the
        # build. Once built, later jobs find them up to date.
        try:
            cerium_targets = _run_build_process_output(
                sys.executable, str(_REPO_DIR / 'utils' / 'cerium_first_targets.py'),
                str(source_tree), '--patches', str(_REPO_DIR / 'patches' / 'cerium'),
                '--patches', str(_ROOT_DIR / 'patches' / 'cerium-windows'),
                '--ninja', str(source_tree / 'third_party' / 'ninja' / 'ninja.exe'),
                '--gn', str(source_tree / 'out' / 'Default' / 'gn.exe')).split()
        except subprocess.CalledProcessError as exc:
            print(f'::warning::Could not list the targets for Cerium\'s changes: {exc}')
            cerium_targets = []
        if cerium_targets:
            run_ninja(*cerium_targets)
            print(f"::notice::Cerium's changes compiled: {len(cerium_targets)} targets")
        else:
            print("::warning::Found no targets for Cerium's changes; "
                  "they are compiled with the rest")

        # Cerium: the browser first, then uBlock Origin and Dark Reader, then
        # the installer, which packs them (chrome.release). The extensions are
        # signed at the end of the build, like on the other platforms, so that
        # the key secrets are used even if they were added during the build.
        run_ninja('chrome', 'chromedriver')
        _bundle_extensions(source_tree / 'out' / 'Default', downloads_cache)
        run_ninja(*targets)
        report_build_errors.report_progress(log_path)
        # package
        os.chdir(_ROOT_DIR)
        subprocess.run([sys.executable, 'package.py', '--cpu-arch', '32bit' if args.x86 else 'arm' if args.arm else '64bit'], check=True)
    else:
        # Cerium: uBlock Origin and Dark Reader, before mini_installer packs them
        _bundle_extensions(source_tree / 'out' / 'Default', downloads_cache)
        _run_build_process(*ninja_commandline, *targets)

if __name__ == '__main__':
    try:
        main()
    except _BuildTimeout:
        sys.exit(_BUILD_TIMEOUT_EXIT_CODE)
