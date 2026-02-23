# Crypto Sniper (Paper Mode Platform)

Paper-mode simulation and reporting platform for testing token sniping rules safely (no live trading, no private keys).

## Quick Start

### 1. Run a paper simulation

```powershell
cd C:\Users\Main_User\Desktop\crypto_sniper
python src/runner/paper_sim_runner.py --steps 50 --seed 1
```

### 2. Run with rule parameters + export JSON/CSV

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

## Notes

- Paper mode only (no live trading).
- Results are deterministic for the same seed and starting DB state.
- Avoid running DB-writing tests in parallel on Windows (`data/sniper.db` can lock).
