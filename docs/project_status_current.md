# Project Status (Current)

Last updated: 2026-02-27
Owner workspace: `C:\Users\Main_User\Desktop\crypto_sniper`

## 1) Executive Snapshot
- Current phase: `V1.6 supervised discovery live`.
- Live capability status: `working` (supervised tiny live entries are executing and finalizing on-chain).
- Release gate status: `NOT READY FOR UNSUPERVISED` (Go/No-Go still false on quality thresholds).
- Scope currently proven:
  - Candidate discovery (DexScreener) -> scored promotion -> one tiny supervised live submit.
  - Postprocess, bundles, performance summary, and go/no-go gating.

## 2) Proven Live Evidence
- Confirmed finalized supervised live submit observed on 2026-02-27:
  - signature: `2vxn8o8sd5jGK3Lkbo9gyAa9jMn1ejDQn5ZToQNEGjcFHyZSHMNvE3MKvPPi4DcynB5RUbM8A8idPfPi6PgmTVs`
  - status: `finalized`, `err=null`
- Candidate selected during supervised discovery flow:
  - token: `PiGEguwVkcy9ed3to9Wpm9cLRPiq8P3tZdVWxjApump` (`SIM`)

## 3) Version Checkpoints
- `V1.5` complete
  - commit: `0696c25`
  - tag: `v1.5.0-supervised-release`
- `V1.6` complete + hotfix
  - commit: `e1cf785` (core v1.6)
  - commit: `1acce15` (DexScreener user-agent + workflow wiring fix)

## 4) Current Gate State (Why not fully autonomous yet)
Latest go/no-go indicates `go=false` with failed checks:
- `min_finalized_count`
- `max_quote_mismatch_rate`
- `max_avg_realized_slippage_bps`

Interpretation:
- Pipeline works, but sample quality/consistency is below promotion threshold for unattended operation.
- System is in `supervised production validation` mode, not autonomous profit mode.

## 5) What the Bot Does Right Now
- Finds candidate pairs from live DexScreener feeds.
- Scores candidates with rule gates.
- Promotes top candidate(s) if gates pass.
- Executes one tiny supervised live buy (guarded caps).
- Exports receipts/bundles/performance/go-no-go evidence.

It does **not** yet run a fully autonomous discover->buy->manage exits strategy for profit.

## 6) One-Command Operational Paths
From `C:\Users\Main_User\Desktop\crypto_sniper`:

- Full V1.6 cycle:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_release_cycle`
- Discovery live only:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_supervised_discovery_live`
- Postprocess only:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_supervised_discovery_postprocess`
- Performance summary only:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_performance_summary`
- Go/No-Go only:
  - `powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset v16_go_no_go_gate`

## 7) Next Milestones (Priority Order)
1. `V1.7-M1`: stabilize discovery promotion quality
- Reduce mismatch rate and slippage drift using tighter promotion thresholds and fallback controls.

2. `V1.7-M2`: introduce supervised exit workflow
- Add explicit, validated exit policy execution path after entry (still guarded/supervised).

3. `V1.7-M3`: prove repeated quality runs
- Achieve go/no-go pass over required finalized sample window.

4. `V1.8`: consider controlled semi-autonomous mode
- Only after sustained gate pass and reviewed risk controls.

## 8) Source of Truth Files
- Status: `docs/project_status_current.md`
- V1.6 runbook: `docs/v16_supervised_discovery_live_runbook.md`
- V1.6 ops hardening runbook: `docs/v16_ops_hardening_runbook.md`
- Preset runner: `examples/run_live_pilot_workflow_preset.ps1`
