<#
.SYNOPSIS
  Smoke test for a PACKAGED N1cePDF build (dist\N1cePDF or an installed copy).
.DESCRIPTION
  1. Confirms N1cePDF.exe is a GUI-subsystem executable (no console window).
  2. Runs `N1cePDF.exe --self-test` with Python removed from PATH/PYTHON* variables, so only the bundled
     runtime can satisfy imports. The self-test opens/edits/saves/re-opens a PDF in a Cyrillic + Romanian
     + spaces path using the bundled PDFium, qpdf, ReportLab, Pillow and Qt, and starts the real main window.
  3. Fails (exit code 1) unless every step reports ok.
.EXAMPLE
  powershell -File packaging\smoke-test.ps1 -ExeDir dist\N1cePDF
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExeDir,
    [int]$TimeoutSec = 240,
    [switch]$NoGui
)
$ErrorActionPreference = 'Stop'
$exe = Join-Path (Resolve-Path -LiteralPath $ExeDir).Path 'N1cePDF.exe'
if (-not (Test-Path -LiteralPath $exe)) { Write-Host "FAIL: $exe not found" -ForegroundColor Red; exit 1 }

# --- 1. PE subsystem must be 2 (WINDOWS_GUI) -------------------------------------------------------------
$fs = [System.IO.File]::OpenRead($exe)
try {
    $buf = New-Object byte[] 4096
    [void]$fs.Read($buf, 0, $buf.Length)
} finally { $fs.Dispose() }
$peOffset = [BitConverter]::ToInt32($buf, 0x3C)
$subsystem = [BitConverter]::ToUInt16($buf, $peOffset + 24 + 68)
if ($subsystem -ne 2) {
    Write-Host "FAIL: N1cePDF.exe subsystem is $subsystem (expected 2 = Windows GUI); a console window would appear." -ForegroundColor Red
    exit 1
}
Write-Host "ok   GUI subsystem executable (no console window)"

# --- 2. run the self-test with a scrubbed environment ----------------------------------------------------
$result = Join-Path ([System.IO.Path]::GetTempPath()) ("n1cepdf-smoke-" + [guid]::NewGuid().ToString('N') + '.json')
$saved = @{}
foreach ($n in 'PATH', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV') { $saved[$n] = [Environment]::GetEnvironmentVariable($n, 'Process') }
try {
    $sys32 = Join-Path $env:SystemRoot 'System32'
    [Environment]::SetEnvironmentVariable('PATH', "$sys32;$env:SystemRoot", 'Process')
    foreach ($n in 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV') { [Environment]::SetEnvironmentVariable($n, $null, 'Process') }
    $argList = @('--self-test', ('"' + $result + '"'))
    if ($NoGui) { $argList += '--no-gui' }
    $p = Start-Process -FilePath $exe -ArgumentList $argList -PassThru
    $null = $p.Handle                               # keeps ExitCode readable on Windows PowerShell 5.1
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        try { $p.Kill() } catch { }
        Write-Host "FAIL: self-test did not finish within $TimeoutSec s" -ForegroundColor Red
        exit 1
    }
    $code = $p.ExitCode
} finally {
    foreach ($n in $saved.Keys) { [Environment]::SetEnvironmentVariable($n, $saved[$n], 'Process') }
}

if (-not (Test-Path -LiteralPath $result)) {
    Write-Host "FAIL: no result file was written (exit code $code). Check %LOCALAPPDATA%\N1cePDF\logs\n1cepdf.log" -ForegroundColor Red
    exit 1
}
$report = Get-Content -LiteralPath $result -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($s in $report.steps) {
    if ($s.ok) { Write-Host ("ok   {0}  ({1}s)" -f $s.step, $s.seconds) }
    else { Write-Host ("FAIL {0}: {1}" -f $s.step, $s.detail) -ForegroundColor Red }
}
Remove-Item -LiteralPath $result -Force -ErrorAction SilentlyContinue
if ($code -ne 0 -or -not $report.ok) { Write-Host "SMOKE TEST FAILED (exit code $code)" -ForegroundColor Red; exit 1 }
Write-Host "SMOKE TEST PASSED - N1cePDF $($report.version), bundled Python $($report.python), frozen=$($report.frozen)" -ForegroundColor Green
exit 0
