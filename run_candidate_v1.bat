@echo off
setlocal

cd /d "%~dp0"

echo Running paper candidate preset (winner) with exports...
python -m src.runner.paper_sim_candidate_runner --preset-name candidate_final_v1_tp_higher_034 %*

endlocal
