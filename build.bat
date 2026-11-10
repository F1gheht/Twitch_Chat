@echo off
setlocal
cd /d "%~dp0"

REM Build TwitchChatOverlay exe + installer.
REM Requires: Python 3.9+ (python.org, "Add to PATH") and Inno Setup 6.
REM Usage: build.bat [version]   e.g. build.bat 1.0.0

set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=1.0.0"
set "LOG=%~dp0build.log"
echo Build log > "%LOG%"

REM ---- find Python ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo [ERROR] Python not found.
  echo Install Python from https://www.python.org/downloads/
  echo and check "Add python.exe to PATH" during install.
  goto :fail
)
echo Using: %PY%
%PY% --version

echo.
echo === [1/3] Installing dependencies ===
%PY% -m pip install --upgrade pip >> "%LOG%" 2>&1
%PY% -m pip install -r requirements.txt pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] pip install failed. See build.log
  goto :fail
)

echo.
echo === [2/3] Building exe (PyInstaller) ===
%PY% -m PyInstaller --noconfirm --clean --windowed --name TwitchChatOverlay --icon assets\icon.ico --add-data "assets;assets" twitch_chat_overlay.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] PyInstaller failed. See build.log
  goto :fail
)
echo OK: dist\TwitchChatOverlay\TwitchChatOverlay.exe

echo.
echo === [3/3] Building installer (Inno Setup) ===
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo [WARN] Inno Setup 6 not found - installer skipped.
  echo Download: https://jrsoftware.org/isdl.php
  echo The program itself is ready in dist\TwitchChatOverlay\
  goto :done
)
"%ISCC%" /DMyAppVersion=%VERSION% installer\setup.iss >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Inno Setup failed. See build.log
  goto :fail
)

echo.
echo DONE! Installer: Output\TwitchChatOverlay-Setup-%VERSION%.exe
goto :done

:fail
echo.
echo Build FAILED. Details in: %LOG%
echo.
pause
exit /b 1

:done
echo.
pause
exit /b 0
