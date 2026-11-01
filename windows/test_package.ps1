<#
.SYNOPSIS
    Tests Cerium's Windows packages the way users install them.

.DESCRIPTION
    1. Installs Cerium with cerium-*-windows-setup.exe, for the current user and
       without questions (as a double click does), and checks the installed
       files (the bundled extensions included), the version information, the
       app icon, the registry entries and the Start menu shortcut.
    2. Starts the installed Cerium: shows a page in headless mode, then opens
       a window with the default profile, which must get uBlock Origin and
       Dark Reader.
    3. Uninstalls Cerium.
    4. Extracts cerium-*-windows.zip and starts Cerium from there, which must
       get the extensions too.

    Stops at the first failed check with exit status 1 and a GitHub Actions
    error annotation. release-windows.yml runs it before publishing.

    With -Upstream it tests ungoogled-chromium-windows's x64 packages instead
    (the same installer and .zip, named Chromium, without bundled extensions
    and with Chromium's icon): test-windows-packaging.yml uses that to test
    changes to this script in minutes.

.EXAMPLE
    windows/test_package.ps1 -Packages build
#>
param(
    # Directory with cerium-<version>-<revision>-x64-windows-setup.exe and
    # cerium-<version>-<revision>-x64-windows.zip
    [Parameter(Mandatory = $true)]
    [string] $Packages,
    # Test ungoogled-chromium-windows's installer and .zip instead
    [switch] $Upstream
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$work = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$work = Join-Path $work 'cerium-test'
New-Item -ItemType Directory -Force -Path $work | Out-Null

function Format-Annotation([string] $Message) {
    return $Message.Replace('%', '%25').Replace("`r", '').Replace("`n", '%0A')
}

function Fail([string] $Message) {
    Write-Host "::error::$(Format-Annotation $Message)"
    exit 1
}

function Notice([string] $Message) {
    Write-Host "::notice::$(Format-Annotation $Message)"
}

function Warn([string] $Message) {
    Write-Host "::warning::$(Format-Annotation $Message)"
}

function Get-Tail([string] $Path, [int] $Lines = 25) {
    if ($Path -and (Test-Path $Path)) {
        return (Get-Content -Path $Path -Tail $Lines -ErrorAction SilentlyContinue) -join "`n"
    }
    return ''
}

function Get-InstallerLog {
    # setup.exe logs to the system's temp directory when the user is an
    # administrator (as on CI machines), else to the user's.
    $directories = @($env:TEMP, (Join-Path $env:SystemRoot 'SystemTemp'), (Join-Path $env:SystemRoot 'Temp'))
    foreach ($directory in $directories) {
        $log = Join-Path $directory 'chromium_installer.log'
        if (Test-Path $log) {
            return "Installer log ($log):`n$(Get-Tail $log 40)"
        }
    }
    return 'No installer log found.'
}

# Runs a program and waits for it; returns its exit status, or $null if it
# was still running after $Seconds (it is then stopped).
function Invoke-Program {
    param(
        [string] $FilePath,
        [string[]] $Arguments,
        [int] $Seconds,
        [string] $StdOut = '',
        [string] $StdErr = ''
    )
    $parameters = @{ FilePath = $FilePath; ArgumentList = $Arguments; PassThru = $true; NoNewWindow = $true }
    if ($StdOut) { $parameters.RedirectStandardOutput = $StdOut }
    if ($StdErr) { $parameters.RedirectStandardError = $StdErr }
    $process = Start-Process @parameters
    $null = $process.Handle  # keeps the exit status readable after it exits
    if (-not $process.WaitForExit($Seconds * 1000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $process.ExitCode
}

# Stops every browser process started from $Directory: asks it to quit (so
# that it saves its settings) unless -Force, and makes sure after 30 seconds.
function Stop-Browser([string] $Directory, [switch] $Force) {
    $processes = @(Get-Process -Name chrome -ErrorAction SilentlyContinue | Where-Object {
            $path = try { $_.Path } catch { $null }
            $path -and $path.StartsWith($Directory, [StringComparison]::OrdinalIgnoreCase)
        })
    if ($processes.Count -eq 0) { return }
    if (-not $Force) {
        foreach ($process in $processes) {
            try { & taskkill.exe /PID $process.Id 2>&1 | Out-Null } catch { }
        }
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline -and @($processes | Where-Object { -not $_.HasExited }).Count -gt 0) {
            Start-Sleep -Seconds 1
        }
    }
    $processes | Where-Object { -not $_.HasExited } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

function Get-Property($Object, [string] $Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $null
}

# Shows a page in headless mode and checks that it was rendered.
function Test-HeadlessPage([string] $Exe, [string] $ProfileDir, [string] $Label) {
    $dom = Join-Path $work "$($Label -replace '\W', '')-dom.html"
    $log = Join-Path $work "$($Label -replace '\W', '')-launch.log"
    $status = Invoke-Program -FilePath $Exe -Seconds 120 -StdOut $dom -StdErr $log -Arguments @(
        '--headless=new', '--no-first-run', "--user-data-dir=`"$ProfileDir`"",
        '--dump-dom', 'data:text/html,<title>ok</title>')
    $html = if (Test-Path $dom) { Get-Content -Raw -Path $dom } else { '' }
    if ($null -eq $html) { $html = '' }
    if ($null -eq $status -or $status -ne 0 -or -not $html.Contains('<title>ok</title>')) {
        $statusText = if ($null -eq $status) { 'did not finish in 2 minutes' } else { "exited with status $status" }
        Fail "$Label $product $statusText without showing the page. Output:`n$(Get-Tail $log 30)"
    }
}

# The IDs of the extensions in a bundled_extensions directory.
function Get-BundledIds([string] $BundleDir) {
    return @(Get-ChildItem -Path $BundleDir -Filter '*.json' | ForEach-Object { $_.BaseName })
}

# Waits until the profile in $ProfileDir has every extension of the
# bundled_extensions directory $BundleDir installed; returns the missing ones.
function Wait-Extensions([string] $ProfileDir, [string] $BundleDir, [int] $Seconds) {
    $ids = @(Get-BundledIds $BundleDir)
    if ($ids.Count -eq 0) { Fail "No extensions in $BundleDir" }
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        $missing = @($ids | Where-Object {
                -not (Test-Path (Join-Path $ProfileDir "Default\Extensions\$_\*\manifest.json"))
            })
        if ($missing.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return , $missing
}

# The names in the manifests of the extensions installed in a profile.
function Get-ExtensionNames([string] $ProfileDir) {
    $names = foreach ($manifest in Get-ChildItem -Path (Join-Path $ProfileDir 'Default\Extensions\*\*\manifest.json') -ErrorAction SilentlyContinue) {
        $json = Get-Content -Raw -Path $manifest.FullName
        foreach ($match in [regex]::Matches($json, '"name"\s*:\s*"([^"]+)"')) { $match.Groups[1].Value }
    }
    return (@($names) | Sort-Object -Unique) -join ', '
}

# The extension IDs pinned to the toolbar in a profile's Preferences (read
# with a regular expression: they may have keys that differ only in case,
# which ConvertFrom-Json refuses).
function Get-PinnedIds([string] $Preferences) {
    $json = Get-Content -Raw -Path $Preferences
    if ($json -match '"pinned_extensions"\s*:\s*\[([^\]]*)\]') {
        return @([regex]::Matches($Matches[1], '"([a-p]{32})"') | ForEach-Object { $_.Groups[1].Value })
    }
    return @()
}

# --- The packages -------------------------------------------------------------

if ($Upstream) {
    $product = 'Chromium'
    $setupPattern = '*installer*x64*.exe'
    $zipPattern = '*windows*x64*.zip'
} else {
    $product = 'Cerium'
    $setupPattern = 'cerium-*-x64-windows-setup.exe'
    $zipPattern = 'cerium-*-x64-windows.zip'
}
$setup = Get-ChildItem -Path $Packages -Filter $setupPattern | Select-Object -First 1
$zip = Get-ChildItem -Path $Packages -Filter $zipPattern | Select-Object -First 1
if (-not $setup -or -not $zip) {
    Fail "Expected $setupPattern and $zipPattern in $Packages; found: $((Get-ChildItem $Packages | ForEach-Object Name) -join ', ')"
}
if ($setup.Name -notmatch '(\d+\.\d+\.\d+\.\d+)-\d+') {
    Fail "No version in the installer's name $($setup.Name)"
}
$version = $Matches[1]
Write-Host "Testing $($setup.Name) ($([math]::Round($setup.Length / 1MB)) MB) and $($zip.Name) ($([math]::Round($zip.Length / 1MB)) MB): $product $version"

# --- 1. Install -----------------------------------------------------------------

$appDir = Join-Path $env:LOCALAPPDATA "$product\Application"
$userData = Join-Path $env:LOCALAPPDATA "$product\User Data"
if (Test-Path $appDir) { Fail "$appDir exists before the installation" }

$status = Invoke-Program -FilePath $setup.FullName -Arguments @('--do-not-launch-chrome', '--verbose-logging') -Seconds 600
if ($null -eq $status) { Fail "The installer did not finish in 10 minutes.`n$(Get-InstallerLog)" }
if ($status -ne 0) { Fail "The installer exited with status $status.`n$(Get-InstallerLog)" }

$exe = Join-Path $appDir 'chrome.exe'
$versionDir = Join-Path $appDir $version
$bundleDir = Join-Path $versionDir 'bundled_extensions'
$expected = @($exe, (Join-Path $versionDir 'chrome.dll'), (Join-Path $versionDir 'Installer\setup.exe'))
if (-not $Upstream) {
    $expected += @((Join-Path $versionDir 'initial_preferences'), (Join-Path $bundleDir 'licenses'))
}
foreach ($path in $expected) {
    if (-not (Test-Path $path)) {
        $found = (Get-ChildItem -Path $appDir -Recurse -Depth 1 -ErrorAction SilentlyContinue | ForEach-Object FullName) -join "`n"
        Fail "The installer did not install $path. Installed:`n$found"
    }
}
$crx = @()
if (-not $Upstream) {
    $crx = @(Get-ChildItem -Path $bundleDir -Filter '*.crx')
    if ($crx.Count -ne 2) { Fail "Expected 2 bundled extensions in $bundleDir, found: $(($crx | ForEach-Object Name) -join ', ')" }
}

$info = (Get-Item $exe).VersionInfo
if ($info.ProductName -ne $product) { Fail "chrome.exe's product name is '$($info.ProductName)', not $product" }
if ($info.ProductVersion -ne $version) { Fail "chrome.exe's version is '$($info.ProductVersion)', not $version" }

# The app icon must be Cerium's, not Chromium's blue one.
$iconReport = $null
try {
    Add-Type -AssemblyName System.Drawing
    $bitmap = [System.Drawing.Icon]::ExtractAssociatedIcon($exe).ToBitmap()
    $opaque = 0; $blue = 0; $orange = 0
    for ($x = 0; $x -lt $bitmap.Width; $x++) {
        for ($y = 0; $y -lt $bitmap.Height; $y++) {
            $pixel = $bitmap.GetPixel($x, $y)
            if ($pixel.A -lt 128) { continue }
            $opaque++
            $hue = $pixel.GetHue(); $saturation = $pixel.GetSaturation(); $brightness = $pixel.GetBrightness()
            if ($hue -ge 190 -and $hue -le 240 -and $saturation -gt 0.35 -and $brightness -gt 0.3 -and $brightness -lt 0.9) { $blue++ }
            if ($hue -ge 15 -and $hue -le 50 -and $saturation -gt 0.5) { $orange++ }
        }
    }
    $iconReport = "$($bitmap.Width)x$($bitmap.Height), $opaque opaque pixels, $blue blue, $orange orange"
} catch {
    Warn "Could not read chrome.exe's icon: $_"
}
if ($iconReport) {
    Write-Host "App icon: $iconReport"
    if ($Upstream) {
        # Chromium's blue icon: what the check for Cerium must catch.
        if ($blue -le $opaque * 0.03) { Fail "The icon check does not see Chromium's blue icon ($iconReport)" }
    } elseif ($opaque -eq 0 -or $blue -gt $opaque * 0.03 -or $orange -eq 0) {
        Fail "chrome.exe's icon does not look like Cerium's ($iconReport)"
    }
}

$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$product"
if (-not (Test-Path $uninstallKey)) { Fail "The installer did not register $product in Apps (no $uninstallKey)" }
$entry = Get-ItemProperty -Path $uninstallKey
$displayName = Get-Property $entry 'DisplayName'
$displayVersion = Get-Property $entry 'DisplayVersion'
if ($displayName -ne $product) { Fail "$product is listed in Apps as '$displayName'" }
if ($displayVersion -ne $version) { Fail "$product is listed in Apps with version '$displayVersion', not $version" }
if (-not (Test-Path "HKCU:\Software\$product")) { Fail "The installer did not create HKCU:\Software\$product" }

$programs = [Environment]::GetFolderPath('Programs')
$shortcut = Join-Path $programs "$product.lnk"
if (-not (Test-Path $shortcut)) {
    $found = (Get-ChildItem -Path $programs -Filter '*.lnk' -Recurse | ForEach-Object FullName) -join ', '
    Fail "No Start menu shortcut $shortcut (found: $found)"
}
$target = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut).TargetPath
if (-not $target.EndsWith("\$product\Application\chrome.exe", [StringComparison]::OrdinalIgnoreCase)) {
    Fail "The Start menu shortcut opens $target"
}
Notice "Installed $product $version for the current user in $appDir, listed in Apps as $displayName, with a Start menu shortcut and $($crx.Count) bundled extensions"

# --- 2. Start the installed browser ---------------------------------------------

Test-HeadlessPage -Exe $exe -ProfileDir (Join-Path $work 'headless-profile') -Label 'The installed'

# A normal window with the default profile, as from the Start menu: its first
# run reads initial_preferences, and it installs the bundled extensions.
$preferences = Join-Path $userData 'Default\Preferences'
$browser = Start-Process -FilePath $exe -ArgumentList 'about:blank' -PassThru
$missing = @()
if ($Upstream) {
    # Nothing to install: give it the time to create the profile.
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Path $preferences) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
} else {
    $missing = Wait-Extensions -ProfileDir $userData -BundleDir $bundleDir -Seconds 120
}
$running = -not $browser.HasExited
Start-Sleep -Seconds 15  # time to save the settings
Stop-Browser $appDir
if (-not (Test-Path $preferences)) {
    Fail "$product did not create its default profile in $userData (still running: $running)"
}
if ($Upstream) {
    Notice "The installed $product starts, shows a page and opens a window with the default profile ($userData)"
} else {
    if ($missing.Count -gt 0) {
        Fail "The bundled extensions $($missing -join ', ') were not installed in the default profile within 2 minutes (installed: $(Get-ExtensionNames $userData); still running: $running)"
    }
    $names = Get-ExtensionNames $userData
    if (-not $names.Contains('uBlock Origin') -or -not $names.Contains('Dark Reader')) {
        Fail "Unexpected extensions in the default profile: $names"
    }
    $pinned = @(Get-PinnedIds $preferences)
    if (@(Get-BundledIds $bundleDir | Where-Object { $pinned -notcontains $_ }).Count -gt 0) {
        Warn "The bundled extensions are not pinned to the toolbar in the new profile (pinned: $($pinned -join ', '))"
    }
    Notice "The installed Cerium starts, shows a page and installs its bundled extensions in the default profile ($userData): $names"
}

# --- 3. Uninstall ------------------------------------------------------------------

$status = Invoke-Program -FilePath (Join-Path $versionDir 'Installer\setup.exe') -Seconds 300 `
    -Arguments @('--uninstall', '--force-uninstall', '--verbose-logging')
# setup.exe may finish removing its own directory from a copy of itself.
$deadline = (Get-Date).AddSeconds(60)
while ((Test-Path $exe) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
# 19: UNINSTALL_SUCCESSFUL, 29: UNINSTALL_REQUIRES_REBOOT
if ($null -eq $status -or ($status -ne 19 -and $status -ne 29)) {
    Fail "Uninstalling exited with status $status.`n$(Get-InstallerLog)"
}
if (Test-Path $exe) { Fail "Uninstalling left $exe" }
if (Test-Path $uninstallKey) { Fail "Uninstalling left $uninstallKey" }
if (Test-Path $shortcut) { Fail "Uninstalling left $shortcut" }
Notice "$product uninstalls cleanly"

# --- 4. The .zip -------------------------------------------------------------------

$unpacked = Join-Path $work 'zip'
Expand-Archive -Path $zip.FullName -DestinationPath $unpacked
$portable = Get-ChildItem -Path $unpacked -Filter 'chrome.exe' -Recurse | Select-Object -First 1
if (-not $portable) { Fail "$($zip.Name) has no chrome.exe" }
$portableDir = $portable.DirectoryName
$expected = if ($Upstream) { @('chrome.dll') } else { @('chrome.dll', 'initial_preferences', 'bundled_extensions') }
foreach ($name in $expected) {
    if (-not (Test-Path (Join-Path $portableDir $name))) { Fail "$($zip.Name) has no $name next to chrome.exe" }
}
if ((Get-Item $portable.FullName).VersionInfo.ProductName -ne $product) { Fail "The .zip's chrome.exe is not $product" }

$zipProfile = Join-Path $work 'zip-profile'
Test-HeadlessPage -Exe $portable.FullName -ProfileDir $zipProfile -Label 'The .zip'
if ($Upstream) {
    Notice "The .zip's $product starts and shows a page"
    exit 0
}
$browser = Start-Process -FilePath $portable.FullName -PassThru -ArgumentList @(
    '--headless=new', '--no-first-run', "--user-data-dir=`"$zipProfile`"", '--remote-debugging-port=0', 'about:blank')
$missing = Wait-Extensions -ProfileDir $zipProfile -BundleDir (Join-Path $portableDir 'bundled_extensions') -Seconds 120
Stop-Browser $portableDir -Force
if ($missing.Count -gt 0) {
    Fail "The .zip's Cerium did not install the bundled extensions $($missing -join ', ') within 2 minutes (installed: $(Get-ExtensionNames $zipProfile))"
}
Notice "The .zip's Cerium starts, shows a page and installs its bundled extensions: $(Get-ExtensionNames $zipProfile)"
