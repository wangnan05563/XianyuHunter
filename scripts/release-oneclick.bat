@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM XianyuHunter one-click build + package + publish to GitHub Releases
REM Pure ASCII entry (English only). All Chinese output is delegated
REM to build-publish.ps1. No project name / path / version hardcoded.
REM Configure everything in config.publish.json (github_release + build_pipeline).
REM
REM Preflight checks (modeled after AirMirror scripts\release.bat):
REM   0. load GH_TOKEN / GITHUB_TOKEN from .env -- fail fast BEFORE a long build
REM   1. git availability (git log for notes, git remote/credential for publish)
REM   2. Windows PowerShell availability (runs build-publish.ps1 + build steps)
REM   3. gh CLI probe (informational only: publishing uses GitHub REST API)
REM   4. config file existence
REM Set NO_PAUSE=1 to skip the pause on exit (for CI / scripted calls).
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"

if not defined CONFIG_PATH set "CONFIG_PATH=%SCRIPT_DIR%config.publish.json"

REM Console output codepage: 65001 so the .ps1 UTF-8 output shows correctly.
chcp 65001 >nul

echo [publish] repo root: %REPO_ROOT%
echo [publish] config   : %CONFIG_PATH%

REM --- 0. Load GitHub token from .env (repo root first, then scripts dir) ---
REM publish-release.py also reads .env, but loading here fails fast BEFORE a
REM potentially 30+ min PyInstaller build, and exports GH_TOKEN downstream.
if not defined GH_TOKEN (
    for /f "tokens=1,* delims==" %%a in ('findstr /i "^GH_TOKEN=" "%REPO_ROOT%\.env" 2^>nul') do set "GH_TOKEN=%%b"
)
if not defined GITHUB_TOKEN (
    for /f "tokens=1,* delims==" %%a in ('findstr /i "^GITHUB_TOKEN=" "%REPO_ROOT%\.env" 2^>nul') do set "GITHUB_TOKEN=%%b"
)
REM Fall back to scripts dir .env
if not defined GH_TOKEN (
    for /f "tokens=1,* delims==" %%a in ('findstr /i "^GH_TOKEN=" "%SCRIPT_DIR%.env" 2^>nul') do set "GH_TOKEN=%%b"
)
if not defined GITHUB_TOKEN (
    for /f "tokens=1,* delims==" %%a in ('findstr /i "^GITHUB_TOKEN=" "%SCRIPT_DIR%.env" 2^>nul') do set "GITHUB_TOKEN=%%b"
)
REM Strip optional surrounding quotes (delayed expansion), then map to GH_TOKEN.
if defined GH_TOKEN set "GH_TOKEN=!GH_TOKEN:"=!"
if defined GITHUB_TOKEN set "GITHUB_TOKEN=!GITHUB_TOKEN:"=!"
if not defined GH_TOKEN if defined GITHUB_TOKEN set "GH_TOKEN=!GITHUB_TOKEN!"
if defined GH_TOKEN (
    echo [OK]   GitHub token loaded from .env / environment
) else (
    echo [WARN] No GH_TOKEN/GITHUB_TOKEN in env or .env; will rely on git credential store
)

REM --- 1. git (hard dep: notes via git log, repo/token via git remote/credential) ---
where git >nul 2>nul
if errorlevel 1 (
    echo [FAIL] git not found in PATH. Install Git for Windows first.
    goto :fail
)
for /f "tokens=*" %%v in ('git --version 2^>nul') do echo [OK]   %%v

REM --- 2. Windows PowerShell (runs build-publish.ps1 and every build step) ---
where powershell >nul 2>nul
if errorlevel 1 (
    echo [FAIL] Windows PowerShell not found in PATH.
    goto :fail
)
echo [OK]   Windows PowerShell available

REM --- 3. gh CLI (informational only -- publishing uses REST, gh is optional) ---
where gh >nul 2>nul
if not errorlevel 1 (
    echo [OK]   gh CLI available ^(optional^)
) else (
    echo [OK]   gh CLI not found; REST + token/credential will be used ^(normal^)
)

REM --- 4. config file ---
if not exist "%CONFIG_PATH%" (
    echo [FAIL] config not found: %CONFIG_PATH%
    goto :fail
)
echo [OK]   config found

set "EXTRA=%*"
echo.
echo [publish] command: powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build-publish.ps1" -ConfigPath "%CONFIG_PATH%" -RepoRoot "%REPO_ROOT%" !EXTRA!
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build-publish.ps1" -ConfigPath "%CONFIG_PATH%" -RepoRoot "%REPO_ROOT%" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo [FAIL] publish pipeline exited with code %CODE%; see output above.
    goto :fail
)

echo.
echo [publish] DONE.
if not defined NO_PAUSE pause
endlocal
exit /b 0

:fail
if not defined NO_PAUSE pause
endlocal
exit /b 1
