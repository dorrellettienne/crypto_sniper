# Project Status (Current)

Last updated: 2026-02-28
Owner workspace: `C:\Users\Main_User\Desktop\crypto_sniper`

## 1) Executive Snapshot
- Current phase: `Final-stage supervised autonomy hardening`.
- Live capability status: `working` (supervised tiny live entries are executing and finalizing on-chain).
- Release gate status: `GO` on current quality window (`v16_go_no_go=true` in latest run artifacts).
- V2 edge layer status: `V2.10 closeout packet active` (`enable_v2_default_live_gate=true`).
- Scope currently proven:
  - Candidate discovery (DexScreener) -> scored promotion -> one tiny supervised live submit.
  - Postprocess, bundles, performance summary, and go/no-go gating.
  - Supervised exit workflow trace/review path with validated exit policy recommendation outputs.
  - Controlled semi-autonomous readiness packet/trend exports.
  - Adaptive learning snapshot exports (run-label calibration + strategy trend + adaptive review packet).
  - Production monitoring/incident packet and rollback/scaling policy exports.
  - Incident acknowledgement enforcement and automated rollback hook evaluations.
  - Security check and release criteria pack exports (fail-closed readiness gate).
  - Operator ack completion loop and automated blocker-clearance recheck cycle.
  - Supervised scale trial readiness + verification cycle.
  - Supervised scale window plan + outcome gate exports.
  - Buy+sell supervised auto-exit path (manual mode `buy_and_sell`) with enforced safety checks.
  - Final-stage readiness packet + one-command readiness check.
  - Local live ops dashboard + online launcher (token-capable).

## 2) Proven Live Evidence
- Confirmed finalized supervised live submit observed on 2026-02-27:
  - signature: `2vxn8o8sd5jGK3Lkbo9gyAa9jMn1ejDQn5ZToQNEGjcFHyZSHMNvE3MKvPPi4DcynB5RUbM8A8idPfPi6PgmTVs`
  - status: `finalized`, `err=null`
- Candidate selected during supervised discovery flow:
  - token: `PiGEguwVkcy9ed3to9Wpm9cLRPiq8P3tZdVWxjApump` (`SIM`)
- Recent supervised autonomous cycle (2026-02-28):
  - status: `success` (`production_cycle_ok`)
  - evidence: `buy_submitted=2`, `sell_submitted=1`, `auto_trades=1`, `auto_sells=1`
  - final stage readiness: `ready_for_final_stage=true`

## 3) Version Checkpoints
- `V1.5` complete
  - commit: `0696c25`
  - tag: `v1.5.0-supervised-release`
- `V1.6` complete + hotfix
  - commit: `e1cf785` (core v1.6)
  - commit: `1acce15` (DexScreener user-agent + workflow wiring fix)

## 4) Current Gate State
Latest go/no-go indicates `go=true` with failed checks:
- none

Interpretation:
- Pipeline works and current quality window is healthy (`finalized_count=6`, mismatch/slippage checks passing).
- System is in `scheduled supervised autonomy` mode with fail-closed behavior (`success`/`no_trade` healthy, nonzero exit incident).

## 5) What the Bot Does Right Now
- Finds candidate pairs from live DexScreener feeds.
- Scores candidates with rule gates.
- Promotes top candidate(s) if gates pass.
- Executes one tiny supervised live buy (guarded caps).
- Exports receipts/bundles/performance/go-no-go evidence.

It does **not** yet run a fully autonomous discover->buy->manage exits strategy for profit.

## 6) One-Command Operational Paths
From `C:\Users\Main_User\Desktop\crypto_sniper`:

- Current stable execution cycle:
  - `python .\examples\run_live_pilot_workflow_preset.py --preset v30_milestone_cycle`
- Security-only check:
  - `python .\examples\run_live_pilot_workflow_preset.py --preset v22_m1_security_check`
- Next expansion milestone cycle (when implemented):
  - `python .\examples\run_live_pilot_workflow_preset.py --preset v31_milestone_cycle`
- Final-stage readiness check:
  - `cmd /c run_final_stage_readiness.cmd`
- Live dashboard (local):
  - `.\open_live_dashboard.bat`
- Live dashboard (local + tunnel launcher):
  - `.\open_live_dashboard_online.bat`

## 7) Security and Reliability Notes (2026-02-28)
- Targeted secret scan completed across repo patterns (private key, webhook, API key markers): no private-key/webhook leaks found in committed code paths.
- Added watchdog timeout to scheduled autonomous runner (`ReleaseCycleTimeoutSeconds`, default 600s) to prevent zombie task hangs.
- Added auto-exit safety enforcement: if buy submit occurs and sell submit evidence is missing within window, run fails closed.

## 8) Next Milestones (Priority Order)
1. `V2.0-M1`: monitoring + incident packet (`completed 2026-02-27`)
- `v20_m1_monitoring_incident` preset exports ops health checks and incident breadcrumbs.

2. `V2.0-M2`: rollback + scaling policy (`completed 2026-02-27`)
- `v20_m2_rollback_scaling` preset exports release-state governance policy.

3. `V2.1-M1`: incident ack enforcement (`completed 2026-02-27`)
- `v21_m1_incident_ack_enforcement` preset exports ack gating status (`blocked_waiting_for_ack` when missing).

4. `V2.1-M2`: automated rollback hooks (`completed 2026-02-27`)
- `v21_m2_automated_rollback_hooks` preset exports rollback trigger evaluation.

5. `V2.2-M1`: security check (`completed 2026-02-27`)
- `v22_m1_security_check` preset operational; current state `attention_required`.

6. `V2.2-M2`: release criteria pack (`completed 2026-02-27`)
- `v22_m2_release_criteria_pack` preset operational; current state `release_ready=false`.

7. `V2.3-M1`: operator ack completion (`completed 2026-02-27`)
- `v23_m1_operator_ack_completion` preset appends operator ack action row.

8. `V2.3-M2`: blocker clearance recheck (`completed 2026-02-27`)
- `v23_m2_blocker_clearance_recheck` reruns v21/v22 and exports clearance summary (`blockers_cleared=true`).

9. `V2.4`: supervised scale trial execution governance (`completed 2026-02-27`)
- `v24_milestone_cycle` executed with `decision=advance_to_supervised_scale_trial_window`.

10. `V2.5-M1`: supervised scale window plan (`completed 2026-02-27`)
- `v25_m1_supervised_scale_window_plan` exports supervised execution limits and command path.

11. `V2.5-M2`: scale window outcome gate (`completed 2026-02-27`)
- `v25_m2_scale_window_outcome_gate` exports decision gate for next-scope promotion.

12. `V2.6`: supervised scale window live execution + closure packet
- Execute one bounded supervised scale-window run and publish closeout packet with rollback fallback recommendation.
  - `completed 2026-02-27` via `v26_milestone_cycle` with `decision=advance_to_v27_supervised_limited_batch_scope`.

13. `V2.7`: supervised limited-batch scope definition + guardrail pack (`completed 2026-02-27`)
- `v27_milestone_cycle` completed with `scope_state=ready_for_supervised_limited_batch`, `decision=approved_for_v28_candidate_design`.

14. `V2.8`: supervised limited-batch execution candidate design (`completed 2026-02-27`)
- `v28_milestone_cycle` completed with `candidate_state=ready_for_supervised_limited_batch_execution`, `decision=approved_for_v29_supervised_limited_batch_execution`.

15. `V2.9`: supervised limited-batch execution packet + closeout gate (`completed 2026-02-27`)
- `v29_milestone_cycle` completed with `packet_state=ready_for_supervised_limited_batch_window`, `decision=approved_for_v30_scale_readiness_design`.

16. `V3.0`: scale readiness design + promotion gate (`completed 2026-02-27`)
- `v30_milestone_cycle` completed with `readiness_state=ready_for_supervised_scale_promotion`, `decision=approved_for_v31_supervised_scale_promotion`.

17. `V3.1`: supervised scale promotion packet + closeout gate (`completed 2026-02-27`)
- `v31_milestone_cycle` completed with `packet_state=ready_for_supervised_scale_promotion_window`, `decision=approved_for_finalization_track_v32`.

18. `V3.2`: finalization track packet + release closeout gate
- Produce finalization evidence packet and final release closeout gate before project final signoff.

## 9) Source of Truth Files
- Status: `docs/project_status_current.md`
- V1.6 runbook: `docs/v16_supervised_discovery_live_runbook.md`
- V1.6 ops hardening runbook: `docs/v16_ops_hardening_runbook.md`
- V1.7 runbook: `docs/v17_supervised_exit_quality_runbook.md`
- V1.8/V1.9 runbook: `docs/v18_v19_milestone_runbook.md`
- V2.0 runbook: `docs/v20_production_framework_runbook.md`
- V2.1 runbook: `docs/v21_incident_ack_rollback_runbook.md`
- V2.2 runbook: `docs/v22_security_release_runbook.md`
- V2.3 runbook: `docs/v23_blocker_clearance_runbook.md`
- V2.4/V2.5 runbook: `docs/v24_v25_scale_window_runbook.md`
- Scope lock policy (historical, lifted): `docs/scope_lock_v30_execution_only.md`
- Preset runner: `examples/run_live_pilot_workflow_preset.ps1`

## 10) Version Roadmap

### Completed / Current
1. `V1.0 - Supervised Tiny Live Pilot`
- First guarded real on-chain tiny submit path.
- Receipt/status validation and strict caps.

2. `V1.1 - Validation Ops Foundation`
- Postprocess automation, daily packet/bundles, and trend summaries.
- Repeatable operator workflow and artifact hygiene.

3. `V1.2 - Scored Discovery Engine`
- Candidate feature extraction + scoring schema.
- Promotion filters and scored outcome calibration summaries.

4. `V1.3 - Feedback Engine`
- Adaptive entry/score-band/exit recommendations from outcomes.
- Strategy trace/review packets and feedback visibility.

5. `V1.4 - Ops-Ready Supervised Cycle`
- Regression/preflight guardrails + cycle signoff/release checkpoint.
- Daily ops bundle evidence for supervised runtime quality.

6. `V1.5 - Supervised Release Closeout`
- Release hardening and milestone closeout discipline.
- Stable supervised tiny live operations baseline.

7. `V1.6 - Supervised Discovery Live (Current)`
- Live DexScreener discovery -> scoring -> promotion -> tiny supervised live submit.
- Performance summary and go/no-go gate integrated.
- Current state: live path works, go/no-go still blocks unsupervised mode.

### Next Versions
8. `V1.7 - Quality Stabilization + Supervised Exit` (operational)
- Promotion quality evidence maintained with scored calibration trend exports.
- Supervised exit workflow artifact path added (strategy trace -> trend -> review packet).

9. `V1.8 - Controlled Semi-Autonomous Trading` (readiness artifacts operational)
- Controlled readiness packet/trend flow added (`v18_*` exports).
- Guardrails remain supervised with strict caps and kill switches.

10. `V1.9 - Adaptive Strategy Learning` (snapshot artifacts operational)
- Run-label adaptive trend exports added (`v19_*`).
- Entry/exit adaptive recommendation packet generated from accumulated outcomes.

11. `V2.0 - Production Trading Framework` (milestone exports operational)
- Monitoring/incident packet operational (`v20_monitoring_incident_packet.*`).
- Rollback/scaling governance policy operational (`v20_rollback_scaling_policy.*`).
- Formal release criteria for higher-notional deployment remains supervised by operator policy.

12. `V2.1 - Control Closure Hardening` (milestone exports operational)
- Incident ack enforcement report operational (`v21_incident_ack_enforcement.*`).
- Automated rollback hook evaluation operational (`v21_automated_rollback_hooks.*`).

13. `V2.2 - Security + Release Gating` (milestone exports operational)
- Security check report operational (`v22_security_check.*`).
- Release criteria pack operational (`v22_release_criteria_pack.*`) with fail-closed gating.

14. `V2.3 - Blocker Clearance Loop` (milestone cycle operational)
- Operator ack completion + recheck cycle operational (`v23_milestone_cycle`).
- Current state from latest run: `security_state=pass`, `release_ready=true`, `blockers_cleared=true`.

15. `V2.4 - Supervised Scale Trial Governance` (milestone cycle operational)
- Scale trial readiness and verification cycle operational (`v24_milestone_cycle`).
- Current state from latest run: `decision=advance_to_supervised_scale_trial_window`.

16. `V2.5 - Supervised Scale Window Plan + Outcome Gate` (milestone cycle wired)
- Plan and outcome gate presets wired (`v25_m1_supervised_scale_window_plan`, `v25_m2_scale_window_outcome_gate`).
- Current state from latest run: `plan_state=ready_for_execution_window`, `decision=eligible_for_v26_scope_definition`.

17. `V2.6 - Supervised Scale Window Execution + Closeout` (milestone cycle operational)
- Execution packet and closeout presets operational (`v26_m1_supervised_scale_window_execution_packet`, `v26_m2_supervised_scale_window_closeout`).
- Current state from latest run: `packet_state=ready_for_supervised_execution_window`, `decision=advance_to_v27_supervised_limited_batch_scope`.
