@echo off
cd /d "%~dp0"
set "PYTHON_CMD="
py -3 -c "import sys" >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  python -c "import sys" >nul 2>nul
  if %errorlevel%==0 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set PYTHON_CMD="%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)
if not defined PYTHON_CMD (
  echo Python 3 is required.
  pause
  exit /b 1
)
%PYTHON_CMD% -m venv .venv
if errorlevel 1 (
  echo Could not create the virtual environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not exist ".env" (
  echo The scanner will reuse the existing VBOS .env when available.
  echo To use separate credentials, copy .env.example to .env and fill it in.
)
".venv\Scripts\python.exe" -m unittest discover -s tests -v
pause
