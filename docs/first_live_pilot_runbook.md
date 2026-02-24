# First Live Pilot Runbook (Stage 4 Scaffold)

This runbook is a safety-first checklist scaffold for the first controlled live pilot.

Status:
- Live execution is still skeleton-only in code at this stage.
- Use this document to prepare rollout discipline before real send/confirm logic is enabled.

## 1. Pre-Start Checks

- Confirm current milestone/tag is frozen and regression suite is green.
- Confirm `LIVE_READINESS_NOTES.md` has been reviewed.
- Confirm `candidate_preset_name` is selected (winner or backup).
- Confirm token allowlist is non-empty and intentionally limited.
- Confirm `max_order_usd_cap` is tiny (pilot-only).
- Confirm `pilot_mode=True`.
- Confirm `live_kill_switch=False` before startup.
- Confirm `audit_log_path` is configured and writable.
- Confirm operator kill-switch procedure is known.

## 2. Pilot Startup Guardrails (Required)

The startup config should make these values explicit:
- mode = `pilot_live`
- candidate preset name
- allowlist size
- `max_order_usd_cap`
- audit log path
- kill switch state

If any of these are unclear, do not start the pilot.

## 3. Startup Command (Template)

Replace placeholders with your actual future live service command when the live adapter is implemented.

```powershell
python -m src.live.live_service `
  --live-enabled `
  --pilot-mode `
  --candidate-preset-name <candidate_final_v1_tp_higher_034> `
  --token-allowlist <TOKEN_A,TOKEN_B> `
  --max-order-usd-cap <tiny_cap> `
  --audit-log-dir data\exports
```

At this stage, use dry-run/pre-live commands for rehearsal.

## 4. Monitoring During Pilot

- Watch audit JSONL logs in `data\exports`
- Load audit logs in `frontend/index.html` (`Load Audit JSONL`)
- Watch for:
  - unexpected `service_error`
  - repeated retries
  - safety/risk blocks
  - any lifecycle path mismatch

## 5. Kill Switch Procedure

If behavior is unexpected:
- Enable kill switch in config / restart with `live_kill_switch=True`
- Stop service process immediately
- Preserve audit logs and exports for review

Do not continue until the issue is understood.

## 6. Post-Run Review

- Save/export audit logs and rollups
- Review:
  - retries
  - failures
  - safety blocks
  - lifecycle event sequences
- Compare observed behavior against expected candidate preset behavior
- Record findings before changing config

## 7. Pilot Progression Rules (Recommended)

- Start with allowlist-only mode
- Keep `max_order_usd_cap` tiny
- Increase scope only after multiple clean pilot sessions
- Change one variable at a time (preset, cap, signal source, adapter behavior)

