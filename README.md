# Crypto Sniper

Crypto Sniper is an experimental research and operations platform for evaluating short-horizon token trading workflows. It combines:

- deterministic paper-trading simulation
- candidate discovery and scoring workflows
- reporting and dashboard tooling
- guarded live-execution scaffolding that expects secrets to be provided through environment variables, never committed to the repository

The project emphasizes repeatable experiments, observable workflows, and safety controls. It is not a plug-and-play trading bot and does not promise profitable results.

## Quick Navigation

- [At a Glance](#at-a-glance)
- [Screenshots](#screenshots)
- [Core Capabilities](#core-capabilities)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Dashboards](#dashboards)
- [Regression Suite](#regression-suite)
- [Repository Guide](#repository-guide)
- [Scope and Safety](#scope-and-safety)

## At a Glance

- Runs deterministic paper-trading simulations and parameterized experiments
- Discovers, filters, and scores token candidates
- Provides guarded execution workflows with fail-closed controls and preflight validation
- Produces structured artifacts for dashboards, run analysis, and operational audits
- Includes regression coverage for simulations, transports, configuration, and live-workflow helpers

| Area | What's included |
| --- | --- |
| Simulation | Deterministic paper-trading runners and parameterized experiments |
| Discovery | Candidate filtering and scoring workflows |
| Ops | Runbooks, preflight checks, release gates, and bounded execution helpers |
| Reporting | HTML dashboards for summaries, bundles, and validation artifacts |
| Quality | Regression coverage across simulation, config validation, transports, and live-workflow helpers |

## Screenshots

### Strategy Review Dashboard

![Strategy review dashboard](docs/images/strategy-review-sample.png)

### Live Ops Dashboard

![Live ops dashboard](docs/images/live-ops-dashboard-crop.png)

### Validation Bundle Viewer

![Validation bundle viewer](docs/images/validation-bundles-sample.png)

## Core Capabilities

- Deterministic paper trading with configurable strategies and seeded market behavior
- Rule-based token discovery, candidate scoring, and safety filtering
- Paper, dry-run, and guarded live execution adapters
- Risk controls, preflight checks, bounded execution, and audit logging
- JSON and CSV exports for run summaries and closed trades
- Browser-based dashboards for strategy, validation, and operational data
- Discord-compatible webhook alerts for operational events
- Automated regression tests and reusable workflow presets

## Tech Stack

- Python
- Pytest
- HTML, CSS, and vanilla JavaScript dashboards
- JSON-based configuration and export artifacts
- PowerShell and batch scripting for local operational workflows

## Scope and Safety

- No private keys, `.env` files, or webhook secrets are committed in this repository.
- Live-operation helpers are present, but they are intentionally structured to read signer material from external environment variables or local key files outside version control.
- Live workflows should only be used after validating configuration, signal quality, fees, slippage, and risk limits in a controlled environment.

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

## Dashboards

### Open the main dashboard

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

## Repository Guide

1. Start with the paper simulation flow in `src/runner/`.
2. Open the dashboards in `frontend/` to explore generated artifacts.
3. Use `examples/` for workflow presets and operational entry points.
4. See `tests/` and `run_regression_tests.py` for expected behavior.
5. Read `LIVE_READINESS_NOTES.md` before working with guarded live workflows.

## Notes

- Deterministic paper simulation is the simplest local starting point.
- Results are deterministic for the same seed and starting DB state.
- Avoid running DB-writing tests in parallel on Windows (`data/sniper.db` can lock).
- Before live integration work, read `LIVE_READINESS_NOTES.md` (fees/slippage realism, signal quality, safety gates, and rollout cautions).
