@echo off
setlocal

cd /d "%~dp0"

set "DASH_PORT=8787"
set "LOCAL_URL=http://127.0.0.1:%DASH_PORT%/"
if not "%LIVE_DASHBOARD_TOKEN%"=="" (
  set "LOCAL_URL=http://127.0.0.1:%DASH_PORT%/?token=%LIVE_DASHBOARD_TOKEN%"
)

echo Starting live dashboard server...
start "Crypto Sniper Live Dashboard Server" cmd /k "cd /d %~dp0 && cmd /c run_live_dashboard.cmd"

timeout /t 2 /nobreak >nul

echo Opening local dashboard: %LOCAL_URL%
start "" "%LOCAL_URL%"

where cloudflared >nul 2>nul
if %errorlevel%==0 (
  echo Starting Cloudflare tunnel...
  echo Watch the tunnel window for the public https://...trycloudflare.com URL.
  if not "%LIVE_DASHBOARD_TOKEN%"=="" (
    echo Remember to append ?token=%LIVE_DASHBOARD_TOKEN% to the public URL.
  )
  start "Crypto Sniper Tunnel (cloudflared)" cmd /k "cloudflared tunnel --url http://127.0.0.1:%DASH_PORT%"
  goto :done
)

where ngrok >nul 2>nul
if %errorlevel%==0 (
  echo Starting ngrok tunnel...
  echo Watch the ngrok window for the public forwarding URL.
  if not "%LIVE_DASHBOARD_TOKEN%"=="" (
    echo Remember to append ?token=%LIVE_DASHBOARD_TOKEN% to the public URL.
  )
  start "Crypto Sniper Tunnel (ngrok)" cmd /k "ngrok http %DASH_PORT%"
  goto :done
)

echo.
echo No tunnel tool found.
echo Install one of these and re-run:
echo   - cloudflared
echo   - ngrok
echo.
echo Dashboard is still running locally at:
echo   %LOCAL_URL%

:done
endlocal

