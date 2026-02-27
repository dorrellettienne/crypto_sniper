# V1.5 Release Closeout

## Scope Freeze
- V1.5 includes supervised entry gating, release checkpointing, ops bundle indexing, and supervised signoff.
- No additional strategy behavior was introduced in this closeout.

## Required Presets
- `v15_supervised_cycle`
- `v15_release_checkpoint`
- `v15_ops_bundle_postprocess`
- `v15_supervised_signoff`
- `v15_release_cycle` (all-in-one)

## Final Artifacts
- `data/exports/v15_entry_readiness.json`
- `data/exports/v15_release_checkpoint.json`
- `data/exports/v15_ops_bundles/index.json`
- `data/exports/v15_supervised_signoff.json`

## Release Readiness
- Latest `v15_release_checkpoint` run: `release_ready=true`
- Latest `v15_supervised_signoff` run: `signoff_ready=true`

## Operational Note
- Full `v15_release_cycle` requires `SOLANA_PILOT_PRIVATE_KEY_B58` to be loaded in the active PowerShell session for guardrail lock checks.
