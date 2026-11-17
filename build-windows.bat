@echo off
rem ---------------------------------------------------------------------------
rem  N1cePDF 1.1.0 - one-command Windows release build.
rem
rem  Usage:   build-windows.bat [-SkipTests] [-InstallTest] [-Publisher "Name"] [-Python C:\path\python.exe]
rem
rem  This file only launches build-windows.ps1 (same folder), which performs every step:
rem    1 verify environment   2 install dependencies   3 run tests (stop on failure)
rem    4 PyInstaller build    5 packaged smoke test    6 Inno Setup compile
rem    7 verify output        8 optional install/uninstall test
rem  The logic lives in PowerShell because it is far less error-prone than batch for these checks.
rem  Windows PowerShell 5.1 (built into Windows 10/11) is sufficient.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-windows.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo build-windows failed with exit code %RC%.
)
endlocal & exit /b %RC%
