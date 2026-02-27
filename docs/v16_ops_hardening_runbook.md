# V1.6 Ops Hardening Runbook

## Scope
- Discovery-based supervised live entry with strict gates.
- Post-run bundling + index.
- Performance summary.
- Go/No-Go gate.

## Milestone Commands

### 1) V1.6 Discovery Live Run
```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_supervised_discovery_live
```

### 2) V1.6 Postprocess + Bundle Index
```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_supervised_discovery_postprocess
```

### 3) V1.6 Performance Summary
```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_performance_summary
```

### 4) V1.6 Go/No-Go Gate
```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_go_no_go_gate
```

## All-In-One Command
```powershell
powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_release_cycle
```

## Key Artifacts
- `data/exports/v16_discovery_candidates.json`
- `data/exports/v16_scored_discovery_report.json`
- `data/exports/v16_selected_promoted_candidate.json`
- `data/exports/v16_bundles/index.json`
- `data/exports/v16_performance_summary.json`
- `data/exports/v16_go_no_go_gate.json`

## Expected Safety Behavior
- If no promoted candidate qualifies, live submission does not run.
- Go/No-Go blocks progression when finalized/mismatch/slippage/streak limits fail.
