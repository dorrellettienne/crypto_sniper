@echo off
setlocal

cd /d "%~dp0"

echo Running live execution preview export (skeleton only)...
python -m src.live.live_execution_preview_export --export-json-dir data\exports --audit-log-dir data\exports %*

echo.
echo Done. Check:
echo   data\exports\live_execution_preview_*.json
echo   data\exports\live_execution_preview_audit_*.jsonl
echo.
pause

