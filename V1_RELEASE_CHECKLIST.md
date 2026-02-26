# V1 Release Checklist (Supervised Tiny Live Pilot)

Scope target: `v1.0.0-supervised-pilot`

## Scope Freeze

- `V1` is supervised tiny live only
- Operator-in-the-loop required
- One-trade cap per run/session
- No unattended/autonomous production use
- No scale-up beyond tiny test size in `V1`

## Pre-Run Environment Checks

- `SOLANA_PILOT_PRIVATE_KEY_B58` is set in the current PowerShell session
- `python .\examples\print_solana_signer_pubkey_from_env.py` returns `"ok": true`
- Derived signer pubkey matches intended funded pilot wallet
- `powershell -ExecutionPolicy Bypass -File .\examples\check_live_workflow_env_and_config.ps1 -ConfigPath .\data\exports\live_pilot_solana_send_pilot_live_enabled_temp.json -ExpectedUsdSize 0.25` returns `"ok": true`

## V1 Validation Evidence (Required)

- Run at least `3` additional tiny live validations using the workflow script
- Each run uses the scripted flow (not manual long command paste)
- Each run produces:
  - live pilot summary output
  - latest status helper confirmation
  - receipt export (`latest_live_receipt.json` / `.md`)
  - daily validation packet (`daily_live_validation_packet.json` / `.md`)

## Run Commands (V1 Standard)

- Tiny live run:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_tiny_live_usdc_workflow.ps1`
- Preset tiny live:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset tiny_live_usdc`
- Status-only:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset status_only`
- Receipt export:
  - `python .\examples\export_latest_live_submit_receipt.py --output-json data/exports/latest_live_receipt.json --output-md data/exports/latest_live_receipt.md`
- Daily packet export:
  - `python .\examples\export_daily_live_validation_packet.py`

## V1 Acceptance Criteria

- `>= 3` real on-chain submitted transactions finalized successfully (RPC `err=null`)
- `>= 3` runs with bot summary showing `chain=live_confirmed_reconciled` (or conclusive confirmed/finalized reconciliation)
- `0` `submit_signer_error` in the final validation set
- `0` signer pubkey / config wallet mismatch incidents in the final validation set
- `0` unsafe cap violations (USD caps remain tiny)
- Receipt export and daily packet export succeed for each validation run
- Operator can run the full workflow from a fresh PowerShell session using scripts and checklist only

## Artifacts To Preserve

- `data/exports/live_pilot_service_auto_window_*.jsonl` (validation run audit logs)
- `data/exports/latest_live_receipt.json`
- `data/exports/latest_live_receipt.md`
- `data/exports/daily_live_validation_packet.json`
- `data/exports/daily_live_validation_packet.md`
- Any guard reports / status helper outputs used for review

## Release Packaging

- Commit `S4-M103` through `S4-M109` changes
- Push to GitHub
- Create release notes summary (supervised live pilot, known limits)
- Tag release:
  - `v1.0.0-supervised-pilot`

## Post-Release Immediate Follow-Up (V1.1 Candidates)

- Reconciliation finality/polling refinements (finalized vs confirmed-only)
- Operator packet/report polish
- Additional safety presets and scripted workflows
- Rate-limited campaign-style live validation batching

