@echo off
setlocal

cd /d "%~dp0"

set "DASH_URL=http://127.0.0.1:8787/"
if not "%LIVE_DASHBOARD_TOKEN%"=="" (
  set "DASH_URL=http://127.0.0.1:8787/?token=%LIVE_DASHBOARD_TOKEN%"
)

echo Starting live dashboard server...
start "Crypto Sniper Live Dashboard Server" cmd /k "cd /d %~dp0 && cmd /c run_live_dashboard.cmd"

timeout /t 2 /nobreak >nul

echo Opening %DASH_URL%
start "" "%DASH_URL%"

endlocal

