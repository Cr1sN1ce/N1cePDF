<#
.SYNOPSIS
  Reproducible Windows release build of N1cePDF: environment check -> dependencies -> tests -> PyInstaller
  -> packaged smoke test -> Inno Setup installer -> output verification.
.DESCRIPTION
  Run on Windows 10/11 x64 with Python 3.12+ (64-bit) and Inno Setup 6.3+ installed.
  The first run needs internet access to download Python packages; the built application does not.
.PARAMETER SkipTests   Skip the pytest run (for iteration only - never for a release build).
.PARAMETER InstallTest After building, also run packaging\installer-test.ps1 (installs/uninstalls for the current user).
.PARAMETER Publisher   Publisher name shown in Settings > Installed apps (default: env N1CEPDF_PUBLISHER, else "N1cePDF").
.PARAMETER Python      Explicit path to python.exe (otherwise py -3.13 / -3.12 / python are tried).
.EXAMPLE
  .\build-windows.ps1
  .\build-windows.ps1 -Publisher "Jane Doe" -InstallTest
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$InstallTest,
    [string]$Publisher = $env:N1CEPDF_PUBLISHER,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$Root = $PSScriptRoot
$Version = '1.1.0'
$Total = 8

function Step([int]$n, [string]$msg) { Write-Host ""; Write-Host "[$n/$Total] $msg" -ForegroundColor Cyan }
function Fail([string]$msg) { Write-Host ""; Write-Host "BUILD FAILED: $msg" -ForegroundColor Red; exit 1 }
function Invoke-Checked([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "command failed with exit code ${LASTEXITCODE}: $exe $($arguments -join ' ')" }
}

# ---------------------------------------------------------------------------------------------------------
Step 1 'Verify the build environment'
if ($env:OS -ne 'Windows_NT') { Fail 'This script builds the Windows installer and must run on Windows.' }
if (-not [Environment]::Is64BitOperatingSystem) { Fail 'A 64-bit Windows installation is required.' }

function Test-Python([string]$cmd, [string[]]$cmdArgs) {
    try {
        $out = & $cmd @cmdArgs -c "import sys,struct; print(int(sys.version_info>=(3,12) and struct.calcsize('P')==8))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq '1')
    } catch { return $false }
}
$py = $null; $pyArgs = @()
if ($Python) {
    if (Test-Python $Python @()) { $py = $Python } else { Fail "-Python '$Python' is not a 64-bit Python 3.12+." }
} else {
    foreach ($c in @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3'), @('python'))) {
        $a = @(); if ($c.Count -gt 1) { $a = $c[1..($c.Count - 1)] }
        if (Test-Python $c[0] $a) { $py = $c[0]; $pyArgs = $a; break }
    }
}
if (-not $py) { Fail 'No 64-bit Python 3.12+ found. Install it from python.org (tick "py launcher") and retry.' }
Write-Host ("Python: " + (& $py @pyArgs --version))

$iscc = $null
$candidates = @($env:ISCC,
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'))
foreach ($c in $candidates) { if ($c -and (Test-Path -LiteralPath $c)) { $iscc = $c; break } }
if (-not $iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) { Fail 'Inno Setup 6 (ISCC.exe) not found. Install it (winget install JRSoftware.InnoSetup) or set the ISCC environment variable.' }
Write-Host "Inno Setup: $iscc"
foreach ($f in 'main.py', 'packaging\N1cePDF.spec', 'packaging\N1cePDF.iss', 'assets\n1cepdf.ico', 'requirements\build.txt') {
    if (-not (Test-Path -LiteralPath $f)) { Fail "missing project file: $f" }
}

# ---------------------------------------------------------------------------------------------------------
Step 2 'Create/update the build virtual environment (.venv-build) and install dependencies'
$venvPy = Join-Path $Root '.venv-build\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPy)) { Invoke-Checked $py ($pyArgs + @('-m', 'venv', '.venv-build')) }
Invoke-Checked $venvPy @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked $venvPy @('-m', 'pip', 'install', '-r', 'requirements\build.txt')
New-Item -ItemType Directory -Force -Path 'release' | Out-Null
& $venvPy -m pip freeze | Set-Content -LiteralPath 'release\build-environment.txt' -Encoding ASCII   # exact versions used

# ---------------------------------------------------------------------------------------------------------
Step 3 'Run the automated tests (build stops on any failure)'
if ($SkipTests) {
    Write-Host 'WARNING: tests skipped by -SkipTests. Do not publish this build.' -ForegroundColor Yellow
} else {
    if (-not $env:QT_QPA_PLATFORM) { $env:QT_QPA_PLATFORM = 'offscreen' }     # GUI tests run headless
    Invoke-Checked $venvPy @('-m', 'pytest', '-q', '-p', 'no:cacheprovider')
}

# ---------------------------------------------------------------------------------------------------------
Step 4 'Build the standalone application with PyInstaller (onedir)'
foreach ($d in 'build\pyinstaller', 'dist') { if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force } }
Invoke-Checked $venvPy @('-m', 'PyInstaller', 'packaging\N1cePDF.spec', '--noconfirm', '--clean',
    '--distpath', 'dist', '--workpath', 'build\pyinstaller')
$exe = Join-Path $Root 'dist\N1cePDF\N1cePDF.exe'
if (-not (Test-Path -LiteralPath $exe)) { Fail "PyInstaller finished but $exe was not created." }
$mb = [math]::Round(((Get-ChildItem -LiteralPath 'dist\N1cePDF' -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "Built dist\N1cePDF\N1cePDF.exe  (folder size: $mb MB)"

# ---------------------------------------------------------------------------------------------------------
Step 5 'Smoke-test the packaged executable (no Python on PATH)'
& powershell -NoProfile -ExecutionPolicy Bypass -File 'packaging\smoke-test.ps1' -ExeDir 'dist\N1cePDF'
if ($LASTEXITCODE -ne 0) { Fail 'packaged smoke test failed (see messages above).' }

# ---------------------------------------------------------------------------------------------------------
Step 6 'Compile the installer with Inno Setup'
$isccArgs = @()
if ($Publisher) { $isccArgs += "/DAppPublisher=$Publisher" }
$isccArgs += 'packaging\N1cePDF.iss'
Invoke-Checked $iscc $isccArgs

# ---------------------------------------------------------------------------------------------------------
Step 7 'Verify the expected output'
$setup = Join-Path $Root "release\N1cePDF-Setup-$Version-x64.exe"
if (-not (Test-Path -LiteralPath $setup)) { Fail "expected installer not found: $setup" }
$size = [math]::Round((Get-Item -LiteralPath $setup).Length / 1MB, 1)
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
$hash | Set-Content -LiteralPath "$setup.sha256" -Encoding ASCII

Step 8 'Optional installer test'
if ($InstallTest) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File 'packaging\installer-test.ps1' -Installer $setup
    if ($LASTEXITCODE -ne 0) { Fail 'installer test failed (see messages above).' }
} else {
    Write-Host 'Skipped (add -InstallTest to install, smoke-test and uninstall for the current user).'
}

Write-Host ""
Write-Host "BUILD SUCCEEDED" -ForegroundColor Green
Write-Host "Installer : $setup ($size MB)"
Write-Host "SHA-256   : $hash"
Write-Host "Next      : work through docs\release-checklist.md on a clean Windows machine before publishing."
if ($SkipTests) { Write-Host 'NOTE      : tests were skipped for this build.' -ForegroundColor Yellow }
exit 0
