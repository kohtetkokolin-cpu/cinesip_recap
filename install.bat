@echo off
rem ============================================================
rem  Cinesip Recap - one-click installer (Windows)
rem  - checks Python, downloads portable ffmpeg (no admin needed)
rem  - creates venv, installs all dependencies, prepares .env
rem ============================================================
chcp 65001 >nul
title Cinesip Recap - Installer
cd /d "%~dp0"

echo.
echo  ===== Cinesip Recap installer =====
echo.

rem ---------- 1. Python ----------
set PYCMD=python
%PYCMD% --version >nul 2>&1
if errorlevel 1 (
  echo  [!] 'python' not on PATH, trying 'py' launcher...
  set PYCMD=py -3
  %PYCMD% --version >nul 2>&1
)
if errorlevel 1 (
  echo  [!] Python not found. Trying winget...
  winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
  set PYCMD=python
  %PYCMD% --version >nul 2>&1
)
if errorlevel 1 (
  echo.
  echo  [X] Python 3.11+ still not found.
  echo      Please install from https://www.python.org/downloads/
  echo      ^(tick "Add python.exe to PATH" during install^), then re-run install.bat
  pause
  exit /b 1
)
echo  [OK] %PYCMD% found
%PYCMD% --version

rem ---------- 2. portable ffmpeg (local folder, no admin) ----------
if exist "ffmpeg\bin\ffmpeg.exe" (
  echo  [OK] ffmpeg already present ^(portable^)
) else (
  echo  [*] Downloading portable ffmpeg ^(~80MB, may take a while on slow internet^)...
  del /q ffmpeg_dl.zip 2>nul
  rem curl.exe is built into Windows 10+ and handles slow connections far better
  rem than PowerShell Invoke-WebRequest (which often stalls).
  where curl >nul 2>nul
  if not errorlevel 1 (
    curl -L --retry 3 --retry-delay 5 -o ffmpeg_dl.zip "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
  ) else (
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'ffmpeg_dl.zip'"
  )
  if errorlevel 1 (
    echo  [X] Download failed or was interrupted.
    echo      Manual fix: download with your browser from
    echo        https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    echo      unzip it, then copy its "bin" folder to "%CD%\ffmpeg\bin"
    echo      ^(so that ffmpeg\bin\ffmpeg.exe exists^), then re-run install.bat
    pause
    exit /b 1
  )
  powershell -NoProfile -Command "Expand-Archive -Path 'ffmpeg_dl.zip' -DestinationPath 'ffmpeg_tmp' -Force"
  for /d %%d in (ffmpeg_tmp\ffmpeg-*) do (
    if not exist "ffmpeg" mkdir "ffmpeg"
    move "%%d\bin" "ffmpeg\bin" >nul
  )
  rmdir /s /q ffmpeg_tmp
  del ffmpeg_dl.zip
  echo  [OK] ffmpeg installed ^(portable, inside app folder^)
)
"ffmpeg\bin\ffmpeg.exe" -version 2>nul | findstr "ffmpeg version" >nul && echo  [OK] ffmpeg works

rem ---------- 3. virtualenv ----------
if not exist "venv\Scripts\activate.bat" (
  echo  [*] Creating virtual environment...
  %PYCMD% -m venv venv
) else (
  echo  [OK] venv exists
)
call venv\Scripts\activate.bat

rem ---------- 4. dependencies ----------
echo  [*] Installing Python packages ^(this takes a few minutes first time^)...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo  [X] pip install failed. Check your internet and re-run install.bat
  pause
  exit /b 1
)
pip install -r requirements-ai.txt
if errorlevel 1 (
  echo  [!] AI packages failed - basic app still works; re-run install.bat later for AI pipeline
) else (
  echo  [OK] AI pipeline packages installed
)

rem ---------- 5. .env ----------
if not exist ".env" (
  copy .env.example .env >nul
  echo  [OK] .env created - ADD YOUR GOOGLE_API_KEY inside it
) else (
  echo  [OK] .env exists
)

rem ---------- 6. fonts hint ----------
if not exist "fonts\*.ttf" (
  echo  [!] No Myanmar font in fonts\ - download Padauk-Regular.ttf from
  echo      https://github.com/silnrsi/font-padauk/releases and drop it into fonts\
)

echo.
echo  ===== Done! =====
echo  1. Open .env and set GOOGLE_API_KEY=your_key ^(free, from https://aistudio.google.com^)
echo  2. Double-click run.bat to start the app, then open http://localhost:8000
echo.
pause
