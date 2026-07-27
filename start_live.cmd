@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_scanner.cmd first.
  pause
  exit /b 1
)
echo LIVE MODE CAN PLACE REAL NSE INTRADAY ORDERS.
echo It requires LIVE_APPROVAL.txt containing LIVE_EQUITY_ORDERS.
".venv\Scripts\python.exe" run_scanner.py --live --confirm-live LIVE_EQUITY_ORDERS
pause

