@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_scanner.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run_scanner.py --paper
pause

