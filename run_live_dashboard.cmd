@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=.
if "%LIVE_DASHBOARD_TOKEN%"=="" (
  python .\examples\live_dashboard_server.py --host 0.0.0.0 --port 8787
) else (
  python .\examples\live_dashboard_server.py --host 0.0.0.0 --port 8787 --access-token "%LIVE_DASHBOARD_TOKEN%"
)
endlocal
