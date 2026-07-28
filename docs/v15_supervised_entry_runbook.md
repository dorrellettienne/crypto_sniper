# V1.5 Supervised Entry Runbook

## Scope
- Use this runbook for **supervised V1.5 entry readiness operations**.
- Keep trade size and caps unchanged while running this flow.

## Preconditions
- Working directory:
  - `<repo-root>`
- Env key loaded in current PowerShell session:
  - `$env:SOLANA_PILOT_PRIVATE_KEY_B58`
- V1.4 release checkpoint has previously passed at least once.

## Command: Single Supervised Cycle + Entry Gate

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v15_supervised_cycle
```

## Command: Entry Gate Only (No New Cycle)

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v15_entry_gate
```

## Command: V1.5 Release Checkpoint

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v15_release_checkpoint
```

## Command: V1.5 Ops Bundle Postprocess

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v15_ops_bundle_postprocess
```

## Command: V1.5 Supervised Signoff

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v15_supervised_signoff
```

## Command: V1.5 Release Cycle (All-in-One)

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v15_release_cycle
```

## Expected Success Signals
- Supervised cycle output includes:
  - `v14-cycle: done`
  - `v15-entry-gate: entry_ready=true`
  - `v15-supervised-cycle: done`
- Entry-only output includes:
  - `v15-entry-gate: entry_ready=true`
- Release checkpoint output includes:
  - `"release_ready": true`
- Ops bundle postprocess output includes:
  - `v15-ops-postprocess: done`
- Supervised signoff output includes:
  - `"signoff_ready": true`
- Release cycle output includes:
  - `v15-release-cycle: done`

## Key Artifacts
- `data/exports/v14_strategy_cycle_log.jsonl`
- `data/exports/v14_release_checkpoint.json`
- `data/exports/v15_entry_readiness.json`
- `data/exports/v15_entry_readiness.md`
- `data/exports/v15_release_checkpoint.json`
- `data/exports/v15_release_checkpoint.md`
- `data/exports/v15_ops_bundles/index.json`
- `data/exports/v15_ops_bundles/index.md`
- `data/exports/v15_supervised_signoff.json`
- `data/exports/v15_supervised_signoff.md`

## Troubleshooting
- If you see `v14_strategy_cycle_failed`:
  - run:
    - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v14_strategy_cycle`
  - fix failures before retrying V1.5.
- If you see `v15_entry_gate_blocked:*`:
  - inspect failed checks in:
    - `data/exports/v15_entry_readiness.json`
  - typical causes:
    - not enough total cycles yet
    - failed/non-finalized recent cycle
    - missing V1.4 release readiness.
