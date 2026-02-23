@echo off
setlocal

cd /d "%~dp0"

echo Starting local dashboard server on http://localhost:8000 ...
start "Crypto Sniper Dashboard Server" cmd /k "cd /d %~dp0 && python -m http.server 8000"

timeout /t 2 /nobreak >nul

echo Opening dashboard...
start "" "http://localhost:8000/frontend/index.html"

endlocal
