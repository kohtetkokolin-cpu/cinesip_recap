@echo off
rem ============================================================
rem  Cinesip Recap - one-click run (Windows)
rem  Double-click this file to start the studio.
rem ============================================================
chcp 65001 >nul
title Cinesip Recap Studio
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
  echo  [X] Not installed yet - double-click install.bat first.
  pause
  exit /b 1
)

rem portable ffmpeg (downloaded by install.bat)
if exist "ffmpeg\bin\ffmpeg.exe" (
  set "FFMPEG_BIN=%CD%\ffmpeg\bin\ffmpeg.exe"
  set "FFPROBE_BIN=%CD%\ffmpeg\bin\ffprobe.exe"
)

call venv\Scripts\activate.bat

echo.
echo  ===== Cinesip Recap Studio =====
echo  Opening http://localhost:8000 ...
echo  Press CTRL+C to stop.
echo.
start "" "http://localhost:8000"
uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause
