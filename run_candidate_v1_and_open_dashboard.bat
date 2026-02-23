@echo off
setlocal

cd /d "%~dp0"

echo Running paper candidate preset (winner) with exports...
python -m src.runner.paper_sim_candidate_runner --preset-name candidate_final_v1_tp_higher_034 %*

if errorlevel 1 (
  echo Candidate run failed. Dashboard will not be opened.
  exit /b 1
)

echo.
echo Starting dashboard...
call "%~dp0open_dashboard.bat"

echo.
echo In the dashboard, use:
echo - Load JSON (single run summary)
echo - Load Preset CSV (batch comparisons)

endlocal
