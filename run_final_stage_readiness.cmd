@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=.
python .\examples\export_final_stage_readiness.py --fail-on-not-ready
exit /b %errorlevel%

