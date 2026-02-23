@echo off
setlocal

cd /d "%~dp0"

echo Running paper simulation and exporting JSON summary...
python src\runner\paper_sim_runner.py --steps 50 --seed 1 --export-json-dir data\exports %*

if errorlevel 1 (
  echo Simulation failed. Dashboard will not be opened.
  exit /b 1
)

echo.
echo Starting dashboard...
call "%~dp0open_dashboard.bat"

echo.
echo In the dashboard, click "Load JSON" and choose the newest file in data\exports\

endlocal
