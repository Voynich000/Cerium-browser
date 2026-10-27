# Cerium Browser

Cerium is a web browser built on [ungoogled-chromium](https://github.com/ungoogled-software/ungoogled-chromium),
with three additions:

- **A "Use Google services" switch.** It is off by default, so Cerium behaves exactly like
  ungoogled-chromium and doesn't contact Google on its own. Turning it on brings back the
  Google integrations that work without Google's private API keys.
- **uBlock Origin built in.** This is the full Manifest V2 version (1.75.0), not uBlock Origin Lite.
- **Dark Reader built in** (4.9.133).

Base: ungoogled-chromium `156.0.8078.47-1` (Chromium 156.0.8078.47). The Linux build
harness is taken from
[ungoogled-chromium-portablelinux](https://github.com/ungoogled-software/ungoogled-chromium-portablelinux)
at the same tag.

## The Google services switch

Find it in **Settings → System → Google services → Use Google services**. Flip it and press
**Relaunch**. You can also force it from the command line with
`--enable-features=GoogleServices` or `--disable-features=GoogleServices`; this overrides the setting.

| | Off (default) | On |
|---|---|---|
| Install from the Chrome Web Store ("Add to Chrome") | blocked | works |
| Extension auto-updates | none | yes |
| Component updates: Widevine DRM (Netflix, Spotify, …), certificate revocation lists, etc. | none | yes |
| Spell-check dictionary downloads | none | yes |
| Google URLs built into the browser (help links, update servers, …) | blocked | go to the real Google host |

When the switch is on, the browser behaves like Chromium for the features above. For example, it
sends the IDs and versions of your extensions to Google when it checks for updates.

### What the switch can't turn on

Some Google features can't come back. They either need Google API keys and OAuth clients,
which Google only issues for Google Chrome, or ungoogled-chromium removes them at compile time:

- Google account sign-in and Sync
- Safe Browsing (compiled out: `safe_browsing_mode=0`)
- Google Translate and push messaging (GCM)
- Google as a built-in search engine. ungoogled-chromium replaces it at build time. You can
  still add it under **Settings → Search engine → Manage search engines → Add**, with URL
  `https://www.google.com/search?q=%s`.

### How it works

ungoogled-chromium uses two techniques:

1. Its patches delete Google features from the code.
2. "Domain substitution" rewrites every Google hostname in the source to an unresolvable name
   (`clients2.google.com` → `clients2.9oo91e.qjz9zk`). The network stack then blocks those
   names with a `trk:` handler.

Cerium leaves both in place, so "off" is stock ungoogled-chromium, and adds a runtime switch on top:

- `net::kGoogleServices` is a `base::Feature`, so every process (browser, renderer,
  network service) sees the same state. The `google_services.enabled` local-state pref is
  turned into a feature override when the browser starts. That's why changing it needs a relaunch.
- `net::UnsubstituteHost()` exactly reverses `domain_regex.list`. While the switch is on:
  - the `trk:` handler answers with a 307 redirect to the real host instead of an error;
  - the browser recognises the Web Store by its real origin (`extension_urls`,
    `ChromeExtensionsClient`);
  - extension API `matches` patterns that domain substitution rewrote (such as
    `webstorePrivate`) also match the real hosts.
- The code that ungoogled-chromium deletes for the Web Store installer, extension updater,
  component updater and spell-check downloads is restored behind `IsGoogleServicesEnabled()`.

| Patch | What it does |
|---|---|
| `patches/cerium/add-google-services-switch.patch` | Feature, pref, startup hook, `UnsubstituteHost()` and its unit test |
| `patches/cerium/redirect-substituted-domains.patch` | `trk:` handler redirects instead of blocking |
| `patches/cerium/restore-chrome-web-store.patch` | Web Store origin, `webstorePrivate`, installer, process isolation |
| `patches/cerium/restore-extension-and-component-updates.patch` | Extension and component update checks |
| `patches/cerium/restore-spellcheck-downloads.patch` | Dictionary downloads |
| `patches/cerium/add-google-services-setting.patch` | The setting in chrome://settings/system |
| `patches/cerium/add-bundled-extensions.patch` | Installs the bundled extensions |

## Built-in uBlock Origin and Dark Reader

`utils/bundle_extensions.py` does the following for each extension in `extensions/extensions.json`:

1. Downloads the pinned release from GitHub and checks its SHA-256.
2. Packs it as a signed CRX3 file.
3. Writes it to `<install dir>/bundled_extensions/`.

`add-bundled-extensions.patch` makes the browser install everything in that folder on every
platform, without the usual "new extension added" prompt. It also writes `initial_preferences`
so that both extensions are pinned to the toolbar in new profiles.

The extensions remain ordinary extensions:

- You can open their popups, change their settings, and disable or remove them in
  chrome://extensions. A removal is remembered.
- New versions arrive with browser updates.
- uBlock Origin updates its filter lists by itself, from the list maintainers rather than Google.

uBlock Origin needs Manifest V2. ungoogled-chromium's `extensions-manifestv2.patch` keeps MV2
working; stock Chrome refuses to install it.

One tweak is applied to Dark Reader: it no longer opens its help page when it is installed on
first run. Tweaks are listed in `extensions.json`, and the build fails if one no longer matches.

### Signing keys: keep them

The extension ID is derived from the key that signs the CRX file. The browser stores each
extension's settings under its ID. If the key changes, users get a "new" uBlock Origin or Dark
Reader and lose their settings.

- **Local builds:** the first build creates `keys/ublock_origin.pem` and `keys/dark_reader.pem`.
  Git ignores them. Back them up.
- **CI:** store them as repository secrets:

  ```sh
  gh secret set CERIUM_UBLOCK_ORIGIN_KEY < keys/ublock_origin.pem
  gh secret set CERIUM_DARK_READER_KEY < keys/dark_reader.pem
  ```

Never publish the keys. Anyone holding them can produce extensions with the same IDs.

## Branding

The browser calls itself Cerium everywhere it names itself: the window title, the About page,
Settings, menus, dialogs and error pages, in all 81 languages.

- `utils/rebrand.py` runs at build time, after domain substitution. It renames the product in
  Chromium's branded string files (`chromium_strings.grd`, `settings_chromium_strings.grdp`,
  `components_chromium_strings.grd`) and in the product names in `BRANDING`.
- Translations are matched to English strings by a fingerprint of the English text. The script
  uses Chromium's own resource tool (grit) to compute each changed string's new fingerprint and
  moves its translations over, renaming the product in them too. It stops the build if any
  translation would be lost.
- `patches/cerium/rebrand-linux-identity.patch` gives Cerium its own Linux identity:
  - profile in `~/.config/cerium` and cache in `~/.cache/cerium`, so it never shares a profile
    with an installed Chromium;
  - `cerium.desktop` as the desktop file, which also sets the window class;
  - `cerium` as the icon name.

Some things deliberately still say Chromium:

- "The Chromium Authors" in the copyright notice on the About page, because that's who holds
  the copyright;
- "Chromium developers" on chrome://internals pages;
- the user agent, which stays the standard Chrome one so websites keep working.

The logo is still Chromium's; Cerium doesn't have its own icon yet.

## Building (Linux)

Requirements:

- A Linux x86_64 or arm64 machine with Docker
- About 16 GB of RAM (32 GB is better) and 100 GB of free disk space
- Time: a full Chromium build takes several hours even on 8–16 cores

```sh
git clone --recurse-submodules <this repository> cerium-browser
cd cerium-browser
scripts/docker-build.sh        # fetch, prune, patch, domain substitution, build, bundle extensions
package/docker-package.sh      # build/release/cerium-<version>-<arch>.AppImage and _linux.tar.xz
```

`scripts/docker-build.sh` builds a Debian image with the toolchain and runs `scripts/build.sh`
inside it. `scripts/build.sh` takes these steps:

1. Downloads the Chromium source tarball.
2. Removes prebuilt binaries.
3. Applies ungoogled-chromium's patches, then `patches/series`. That's the portablelinux build
   fixes followed by Cerium's patches.
4. Runs domain substitution.
5. Renames the browser to Cerium (`utils/rebrand.py`).
6. Runs `utils/bundle_extensions.py`.
7. Builds `chrome`.

### GitHub Actions

`.github/workflows/build.yml` builds x86_64 and arm64 on the free GitHub runners. The build runs
as a chain of jobs, each under the 6-hour limit, with the build tree passed between them. A
release is created when you push a tag such as `156.0.8078.47-1`. You can also start the
workflow by hand from the Actions tab; that produces build artifacts but no release.

`.github/workflows/check.yml` runs on every push and pull request and takes about 15 minutes.
It runs the tooling tests and applies the whole patch stack and domain substitution to the
real Chromium source (`scripts/check-patches.sh`). You can run that script locally too; it
needs python3, GNU patch and about 20 GB of disk.

## Updating

1. Move the `ungoogled-chromium` submodule to the new tag.
2. Copy that tag's patches and series entries from ungoogled-chromium-portablelinux into
   `patches/ungoogled-chromium/portablelinux/` and `patches/series`.
3. Refresh `patches/cerium/*.patch` if they no longer apply. They are small and each has a
   description header.
4. Run `python3 utils/check_domain_unsubstitution.py`. Add `--source-tree <chromium src>` for
   the full check. This catches changes to `domain_regex.list`.
5. To update an extension, change `version`, `url` and `sha256` in `extensions/extensions.json`.
   Check that its tweaks still apply.

## What has been verified

- The whole patch stack applies cleanly to the Chromium 156.0.8078.47 source tarball. The order
  is: binary pruning, ungoogled-chromium's 110 patches, the portablelinux patches, Cerium's
  patches, then domain substitution. Each step uses ungoogled-chromium's own tools.
- `UnsubstituteHost()` was checked against every substituted hostname in the Chromium 156
  source, about 1,700 cases, with no mismatches. Its gtest passes against Chromium's bundled
  googletest.
- The CRX3 packer was tested in a real Chromium (Chrome for Testing 153):
  - the bundled Dark Reader installed with no prompt, enabled and pinned;
  - uBlock Origin's CRX was accepted, then refused only because stock Chrome has no MV2.
- The changed Settings TypeScript files parse cleanly.
- `utils/rebrand.py` was run on the real Chromium 156 strings. It renamed 611 messages and
  moved about 46,000 translations to their new fingerprints, with none lost. Spot checks in
  German, Japanese, Chinese, Russian, Arabic and Hindi show "Cerium". A second run changes
  nothing.
- **Not done yet: compiling.** A Chromium build needs about 100 GB and hours of CPU time, so the
  C++ and TypeScript changes have not been compiled. They were written against the exact
  Chromium 156 sources, but the first CI build is the real test.

## Platforms

Linux (x86_64, arm64) is the supported target. The patches are not Linux-specific; for example,
the bundled-extension folder is resolved per platform. Windows and macOS builds would need the
ungoogled-chromium-windows or -macos build harness plus `patches/cerium/`.

## Repository layout

```
patches/series                 patch order (portablelinux fixes, then Cerium)
patches/cerium/                 Cerium's Chromium patches
patches/ungoogled-chromium/    Linux build fixes from ungoogled-chromium-portablelinux
extensions/extensions.json     pinned uBlock Origin / Dark Reader releases
extensions/licenses/           their licenses (shipped with the browser)
utils/bundle_extensions.py     download, verify, sign and stage the extensions
utils/crx3.py                  CRX3 packer and verifier
utils/check_domain_unsubstitution.py  checks UnsubstituteHost() against domain_regex.list
utils/test_crx3.py             tests for the CRX3 packer
utils/rebrand.py               renames Chromium to Cerium in the UI strings
scripts/check-patches.sh       applies all patches without building
scripts/, docker/, package/    build and packaging (from portablelinux)
.github/                       CI (from portablelinux)
ungoogled-chromium/            submodule, pinned to 156.0.8078.47-1
```

## License

The patches and scripts are BSD-3-Clause; see [LICENSE](LICENSE). Chromium, ungoogled-chromium
and the bundled extensions keep their own licenses: uBlock Origin is GPL-3.0-or-later and Dark
Reader is MIT.
