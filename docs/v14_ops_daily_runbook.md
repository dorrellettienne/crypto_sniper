# V1.4 Ops Daily Runbook

## Scope
- Use this runbook for **ops-ready supervised trading only**.
- No strategy feature changes while executing this runbook.

## Preconditions
- Working directory:
  - `C:\Users\Main_User\Desktop\crypto_sniper`
- Env key loaded in current PowerShell session:
  - `$env:SOLANA_PILOT_PRIVATE_KEY_B58`

## Daily Cycle Command
Run one guarded cycle (includes regression smoke + guardrail lock + strategy flow + signoff + ops bundle + release checkpoint):

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v14_strategy_cycle
```

## Release Checkpoint Command
Recompute release readiness without running a new cycle:

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v14_release_checkpoint
```

## Expected Success Signals
- Cycle run output includes:
  - `v14-cycle: regression-smoke preflight check`
  - `v14-cycle: guardrail-lock preflight check`
  - `v14-cycle: finalized signature=...`
  - `v14-ops-postprocess: done`
  - `v14-cycle: done`
- Release checkpoint output includes:
  - `"release_ready": true`
  - `"failed_checks": []`

## Key Artifacts
- Cycle log:
  - `data/exports/v14_strategy_cycle_log.jsonl`
- Signoff summary:
  - `data/exports/v14_strategy_cycle_signoff.json`
  - `data/exports/v14_strategy_cycle_signoff.md`
- Release checkpoint:
  - `data/exports/v14_release_checkpoint.json`
  - `data/exports/v14_release_checkpoint.md`
- Ops bundle index:
  - `data/exports/v14_ops_bundles/index.json`
  - `data/exports/v14_ops_bundles/index.md`

## Troubleshooting
- If you see `v14_regression_smoke_failed`:
  - run `powershell -ExecutionPolicy Bypass -File .\examples\run_v14_regression_smoke.ps1`
  - resolve failing check before cycling.
- If you see `v14_guardrail_lock_failed`:
  - run `powershell -ExecutionPolicy Bypass -File .\examples\check_live_workflow_env_and_config.ps1 -ConfigPath data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json -ExpectedUsdSize 0.25`
  - fix config/key mismatch before cycling.
- If PowerShell says `Missing an argument for parameter 'Preset'`:
  - command got line-broken; re-run as one single line.

