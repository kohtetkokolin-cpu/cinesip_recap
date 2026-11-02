@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No .venv found. Running setup.bat first...
  call setup.bat || exit /b 1
)

echo Cinesip Recap  -^>  http://127.0.0.1:7861
".venv\Scripts\python.exe" app.py
