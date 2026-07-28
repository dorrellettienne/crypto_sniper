# Crypto Sniper

Research and operations platform for evaluating short-horizon token trading workflows with a strong emphasis on simulation, guarded execution scaffolding, analytics, and operational safety controls.

This repository is best understood as an engineering project, not a "plug-and-play trading bot." It combines:
- deterministic paper-trading simulation
- candidate discovery and scoring workflows
- reporting and dashboard tooling
- guarded live-execution scaffolding that expects secrets to be provided through environment variables, never committed to the repository

## At a Glance

- Built a multi-layer Python project spanning simulation, data pipelines, operational tooling, dashboards, and safety checks
- Added guarded execution workflows with fail-closed controls, preflight validation, and release-gate style checkpoints
- Created artifact-driven review flows for summaries, bundles, and run analysis through browser-based dashboards
- Backed the workflow with a substantial regression suite covering simulation behavior, live-workflow helpers, transports, and safety-profile validation

## Screenshots

### Strategy Review Dashboard

![Strategy review dashboard](docs/images/strategy-review-sample.png)

### Live Ops Dashboard

![Live ops dashboard](docs/images/live-ops-dashboard-crop.png)

### Validation Bundle Viewer

![Validation bundle viewer](docs/images/validation-bundles-sample.png)

## Why This Is a Strong Portfolio Project

- It shows end-to-end product thinking rather than just isolated scripts.
- It demonstrates engineering judgment around risk, observability, and operational safeguards.
- It gives reviewers several concrete surfaces to inspect: code structure, tests, dashboards, configs, and runbooks.
- It is a better example of systems design and workflow automation than a typical toy trading bot repository.

## What This Project Demonstrates

- Python application design across simulation, data, reporting, and ops layers
- rule-based candidate scoring and experiment workflows
- dashboard-style artifact review for run summaries and trading evidence
- regression testing and release-checkpoint discipline
- safety-oriented automation patterns such as fail-closed checks, preflight gates, and bounded execution paths

## Tech Stack

- Python
- Pytest
- HTML, CSS, and vanilla JavaScript dashboards
- JSON-based configuration and export artifacts
- PowerShell and batch scripting for local operational workflows

## Scope and Safety

- No private keys, `.env` files, or webhook secrets are committed in this repository.
- Live-operation helpers are present, but they are intentionally structured to read signer material from external environment variables or local key files outside version control.
- The safest way to review this project is as a portfolio piece showing systems design, workflow orchestration, and operational guardrails.

## Quick Start

### 1. Clone and enter the repository

```powershell
git clone https://github.com/dorrellettienne/crypto_sniper.git
cd crypto_sniper
```

### 2. Install dependencies

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Run a paper simulation

```powershell
python src/runner/paper_sim_runner.py --steps 50 --seed 1
```

### 4. Run with rule parameters and export JSON/CSV

```powershell
python src/runner/paper_sim_runner.py `
  --steps 100 `
  --seed 7 `
  --usd-size 100 `
  --stop-loss-percent 0.15 `
  --sell-price 0.03 `
  --p-buy 0.35 `
  --p-stop-loss 0.20 `
  --p-sell 0.25 `
  --p-stop-check 0.10 `
  --p-time-exit 0.10 `
  --export-json-dir data\exports `
  --export-csv-dir data\exports `
  --export-trades-csv-dir data\exports
```

Outputs can include:
- Summary JSON (for dashboard)
- Summary CSV
- Closed-trades CSV (one row per closed trade)

## Architecture Overview

- `src/runner/`: paper simulation runners and experiment helpers
- `src/live/`: guarded live-workflow scaffolding, audit logging, and execution adapters
- `frontend/`: HTML dashboards for reviewing exports and operational artifacts
- `examples/`: reusable scripts and workflow presets
- `tests/`: regression coverage for simulation, filters, profiles, and live-workflow helpers
- `config/`: tuned presets and safety profiles

## Dashboard

### Open dashboard (manual)

Open `frontend/index.html` in a browser, or run:

```powershell
python -m http.server 8000
```

Then browse to:

`http://localhost:8000/frontend/index.html`

### Windows shortcuts

- `open_dashboard.bat` -> starts local server and opens the dashboard
- `run_sim_and_open_dashboard.bat` -> runs a simulation export and opens the dashboard

## Dashboard Workflows

### Single run view

1. Export a JSON summary (`--export-json-dir` or `--export-json-path`)
2. Open the dashboard
3. Click `Load JSON`
4. Select the exported `.json` file from `data\exports`

### Compare runs view

1. Generate multiple JSON exports with different seeds/settings
2. Open the dashboard
3. Use `Compare Runs` / multi-file load
4. Select multiple summary `.json` files
5. Compare PnL, trades, win rate, and action counts side-by-side

## Experiments (Seed Sweeps)

Programmatic helper module:

- `src/runner/paper_sim_experiments.py`

Use it to run batches of seeded simulations and summarize results for rule tuning.

## Regression Suite

Run the curated regression suite:

```powershell
python run_regression_tests.py
```

Current expected checkpoint (latest verified):
- `128 passed`
- `Exit Code: 0`

## Recommended Review Path

If you are reviewing this repository for engineering quality, the best order is:
1. Read this README for scope and architecture.
2. Run the paper simulation flow.
3. Open the dashboards in `frontend/`.
4. Inspect `tests/` and `run_regression_tests.py`.
5. Browse `src/live/` to see the guarded execution and safety-oriented workflow design.

## Portfolio Notes

- This project is strongest as a showcase of systems thinking, release discipline, analytics, and safety controls.
- The repository includes live-trading scaffolding, but it should be presented as supervised/guarded infrastructure rather than autonomous production trading software.
- If you share this with employers, point them to the simulation flows, dashboards, tests, and runbooks first.

## Notes

- Deterministic paper simulation is the easiest path for local review.
- Results are deterministic for the same seed and starting DB state.
- Avoid running DB-writing tests in parallel on Windows (`data/sniper.db` can lock).
- Before live integration work, read `LIVE_READINESS_NOTES.md` (fees/slippage realism, signal quality, safety gates, and rollout cautions).
