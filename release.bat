@echo off
rem ===================================================================
rem  HoloTool - one double-click release
rem
rem  What it does, in order:
rem    1. read the current version from app\src\version.py
rem    2. keep the CURRENT build as F:\holotool-test-<current version>
rem       (that copy is what you use to test the update: make_release
rem        overwrites app\dist\HoloTool with the NEW version, so without
rem        a copy there is no "old version" left to update from)
rem    3. bump the patch number
rem    4. build the exe, make the update zip and its .sha256
rem    5. open Explorer on the two files, and open the GitHub
rem       "new release" page with the tag already filled in
rem
rem  ASCII only on purpose: cmd is not UTF-8 by default and a batch file
rem  with Chinese in it fails to parse on some machines.
rem ===================================================================
setlocal
cd /d "%~dp0"

set "PY=%~dp0app\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo ============================================================
echo   HoloTool release
echo ============================================================
echo.

rem --- 1. current version -------------------------------------------
set "OLDVER="
for /f "delims=" %%v in ('"%PY%" app\packaging\bump_version.py --print') do set "OLDVER=%%v"
if "%OLDVER%"=="" (
  echo [ERROR] could not read the current version. Is Python working?
  echo         "%PY%"
  goto end
)
echo Current version : %OLDVER%

rem --- 2. keep the current build as the update-test copy ------------
set "TESTDIR=%~d0\holotool-test-%OLDVER%"
if exist "app\dist\HoloTool\HoloTool.exe" (
  echo Saving current build as %TESTDIR%
  robocopy "app\dist\HoloTool" "%TESTDIR%" /E /R:1 /W:1 /NFL /NDL /NJH /NJS >nul
  if errorlevel 8 echo [WARN] could not copy the current build - test folder may be incomplete
) else (
  echo [note] no existing build in app\dist\HoloTool - skipping the test copy
  set "TESTDIR="
)

rem --- 3. bump ------------------------------------------------------
set "NEWVER="
for /f "delims=" %%v in ('"%PY%" app\packaging\bump_version.py --bump') do set "NEWVER=%%v"
if "%NEWVER%"=="" (
  echo [ERROR] version bump failed - nothing was built
  goto end
)
echo New version     : %NEWVER%
echo.

rem --- 4. build + zip + sha256 --------------------------------------
"%PY%" app\packaging\make_release.py
if errorlevel 1 (
  echo.
  echo [ERROR] make_release.py failed. Scroll up for the real message.
  goto end
)

set "ZIP=%~dp0app\dist\HoloTool-%NEWVER%.zip"
if not exist "%ZIP%" (
  echo [ERROR] expected %ZIP% but it is not there
  goto end
)

rem --- 5. hand it over ----------------------------------------------
echo.
echo ============================================================
echo   Built v%NEWVER%
echo.
echo   Upload these two files to the GitHub release:
echo     HoloTool-%NEWVER%.zip
echo     HoloTool-%NEWVER%.zip.sha256
echo.
if not "%TESTDIR%"=="" echo   Test the update from: %TESTDIR%\HoloTool.exe
echo ============================================================
echo.
echo Opening the folder and the GitHub release page...
explorer /select,"%ZIP%"
rem The URL is inside double quotes, so & is already literal there.
rem Do NOT write ^& - inside quotes the caret is passed through as a
rem character and GitHub receives a broken query string.
start "" "https://github.com/sumy1002/holotool/releases/new?tag=v%NEWVER%&title=v%NEWVER%"

:end
echo.
pause
