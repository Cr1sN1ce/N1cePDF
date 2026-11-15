<#
.SYNOPSIS
  Optional end-to-end installer test: silent per-user install into a custom folder with both shortcuts,
  verify files/shortcuts, run the packaged smoke test against the INSTALLED copy, silent uninstall, verify removal.
.NOTES
  Installs and removes N1cePDF for the current user only (/CURRENTUSER). Run on a Windows machine.
  It checks the installer mechanics; the interactive wizard pages still need the manual checklist
  (docs\release-checklist.md).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    # Spaces, parentheses and Cyrillic letters (built from code points so this file stays pure ASCII).
    [string]$InstallDir = (Join-Path $env:TEMP ('N1cePDF Install Test (' + [string]::new([char[]](0x0442, 0x0435, 0x0441, 0x0442)) + ')'))
)
$ErrorActionPreference = 'Stop'
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$log = Join-Path $env:TEMP 'n1cepdf-install-test.log'
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'N1cePDF.lnk'
$startLnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'N1cePDF.lnk'
$doc = Join-Path $env:TEMP 'n1cepdf-user-document.pdf'
$problems = New-Object System.Collections.Generic.List[string]
function Check($cond, $msg) { if ($cond) { Write-Host "ok   $msg" } else { Write-Host "FAIL $msg" -ForegroundColor Red; $problems.Add($msg) } }

if (Test-Path -LiteralPath (Join-Path $InstallDir 'N1cePDF.exe')) { throw "$InstallDir already contains an installation; choose another -InstallDir." }
Set-Content -LiteralPath $doc -Value '%PDF-1.4 user document that must survive uninstall' -Encoding ASCII

# --- install ---------------------------------------------------------------------------------------------
$inst = Start-Process -FilePath $Installer -Wait -PassThru -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
    ('/DIR="' + $InstallDir + '"'), '/TASKS="desktopicon,startmenuicon"', ('/LOG="' + $log + '"'))
Check ($inst.ExitCode -eq 0) "installer exit code 0 (got $($inst.ExitCode); log: $log)"
$exe = Join-Path $InstallDir 'N1cePDF.exe'
Check (Test-Path -LiteralPath $exe) "N1cePDF.exe installed in custom folder"
Check (Test-Path -LiteralPath (Join-Path $InstallDir 'unins000.exe')) "uninstaller present"
Check (Test-Path -LiteralPath $desktopLnk) "desktop shortcut created"
Check (Test-Path -LiteralPath $startLnk) "Start Menu shortcut created"
Check (-not (Test-Path 'HKCU:\Software\Classes\N1cePDF.Document')) "no file association without the task"
if (Test-Path -LiteralPath $desktopLnk) {
    $target = (New-Object -ComObject WScript.Shell).CreateShortcut($desktopLnk).TargetPath
    Check ($target -eq $exe) "desktop shortcut points to the installed exe"
}

# --- run the packaged self-test against the installed copy ------------------------------------------------
& (Join-Path $PSScriptRoot 'smoke-test.ps1') -ExeDir $InstallDir
Check ($LASTEXITCODE -eq 0) "smoke test of installed copy"

# --- uninstall ---------------------------------------------------------------------------------------------
$un = Join-Path $InstallDir 'unins000.exe'
if (Test-Path -LiteralPath $un) {
    Start-Process -FilePath $un -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait | Out-Null
    $deadline = (Get-Date).AddSeconds(90)           # the uninstaller re-launches itself from %TEMP%; wait for removal
    while ((Test-Path -LiteralPath $exe) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
}
Check (-not (Test-Path -LiteralPath $exe)) "N1cePDF.exe removed by uninstall"
Check (-not (Test-Path -LiteralPath $desktopLnk)) "desktop shortcut removed"
Check (-not (Test-Path -LiteralPath $startLnk)) "Start Menu shortcut removed"
Check (Test-Path -LiteralPath $doc) "user PDF document untouched"
Remove-Item -LiteralPath $doc -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue

if ($problems.Count -gt 0) { Write-Host "INSTALLER TEST FAILED: $($problems.Count) problem(s)" -ForegroundColor Red; exit 1 }
Write-Host "INSTALLER TEST PASSED" -ForegroundColor Green
exit 0
