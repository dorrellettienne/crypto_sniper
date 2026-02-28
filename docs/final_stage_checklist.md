# Final Stage Checklist

Use this checklist to decide when the bot is ready for final production signoff.

## Scope Lock
- No new strategy features or indicator changes.
- Only reliability/ops/security fixes are allowed.

## Required Artifacts
- `data/exports/v16_autonomous_run_summary.json`
- `data/exports/v16_go_no_go_gate.json`
- `data/exports/v2_security_preflight.json`
- `data/exports/v2_closeout_packet.json`
- Latest `data/exports/live_pilot_service_auto_window_*.jsonl`

## Final Criteria
- Latest autonomous summary status is `success`.
- Gate, security preflight, and closeout are all true.
- At least one live trade was submitted in the latest supervised cycle.
- If auto-exit is enabled:
- `manual_submit_mode` allows buy and sell.
- Sell submit evidence exists for submitted buys.
- No non-success sell submit reasons in rollup.
- No `auto_exit_safety_failed` reason.

## One Command (Pass/Fail)
```powershell
python .\examples\export_final_stage_readiness.py --fail-on-not-ready
```

Exit code:
- `0`: ready for final stage
- `2`: not ready yet (check failed checks in output JSON/MD)

Outputs:
- `data/exports/final_stage_readiness.json`
- `data/exports/final_stage_readiness.md`

