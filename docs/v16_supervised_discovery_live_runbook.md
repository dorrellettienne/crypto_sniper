# V1.6 Supervised Discovery Live Runbook

## Scope
- Fetch live DexScreener Solana pairs.
- Apply strict scored-discovery promotion gates.
- Execute exactly one tiny live trade only if at least one candidate is promoted.

## Preconditions
- Working directory:
  - `<repo-root>`
- Env key loaded in active PowerShell:
  - `$env:SOLANA_PILOT_PRIVATE_KEY_B58`
- Existing tiny live config present:
  - `data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json`

## One Command

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_supervised_discovery_live
```

## What It Produces
- `data/exports/v16_discovery_candidates.json`
- `data/exports/v16_scored_discovery_report.json`
- `data/exports/v16_scored_discovery_report.md`
- `data/exports/v16_selected_promoted_candidate.json`

## Default Strict Gates
- `min_liquidity_usd = 25000`
- `max_pair_age_seconds = 900`
- `min_volume_5m_usd = 6000`
- `max_abs_price_change_5m_pct = 18`
- `promote_min_score_total = 30`
- `promote_max_candidates = 1`

## Expected Outcomes
- If no candidate passes, run stops with:
  - `v16_no_promoted_candidates_after_scoring`
- If candidate passes, one tiny supervised live submission is attempted with:
  - `usd_size = 0.25`
  - normal status check output (`check_latest_live_submit_signature_status.py`)
