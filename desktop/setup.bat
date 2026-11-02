@echo off
setlocal
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [warn] ffmpeg not found on PATH. Install it first: choco install ffmpeg
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv || goto :fail
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

echo.
echo Setup complete. Run run.bat
exit /b 0

:fail
echo.
echo Setup failed.
exit /b 1
