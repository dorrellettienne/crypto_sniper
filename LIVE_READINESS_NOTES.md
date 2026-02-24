# Live Readiness Notes

Key reminders before moving from paper/pre-live into real-money execution.

## 1. Paper PnL is Optimistic
- Current paper/pre-live results do not fully model:
  - DEX fees
  - slippage / price impact
  - network / priority fees
  - partial fills
  - latency / stale quotes
- Use current PnL for ranking/tuning, not final profit expectations.
- Priority future milestone: `Cost Model V1` (gross vs net PnL).

## 2. Signal Quality Matters Most
- Sniping performance will depend heavily on:
  - signal speed
  - token safety filtering
  - entry timing quality
- Strong architecture is already in place, but real signal provider quality will drive outcomes.

## 3. Safety Filter vs Risk Engine (Keep Separate)
- Safety filter answers: "Should we consider this token at all?"
- Risk engine answers: "Should we place this trade now?"
- Keep both layers explicit and independent.

## 4. Idempotency Must Eventually Survive Restarts
- Current idempotency is in-memory (good for pre-live/dry-run).
- Live mode will need persistent duplicate protection across restarts/crashes.

## 5. Live Execution Requires Confirmation + Reconciliation
- "Submitted" is not the same as "filled/confirmed."
- Need robust handling for:
  - ambiguous outcomes
  - delayed confirmations
  - retry vs duplicate-send risk
- Order lifecycle + retry policy foundation is already in place.

## 6. Start Live with a Constrained Rollout
- Tiny USD cap
- Strict token allowlist
- Kill switch ready
- Prefer shadow/dry-run parity checks first
- Monitor audit logs + rollups during every session

## 7. Keep Freezing Milestones
- Create checkpoints/tags before risky integration changes.
- This has already been a strength of the project; keep doing it.

## 8. Protect the Regression Suite
- The regression suite is now a core safety asset.
- Add tests for every risky live-path feature before enabling real execution.

## Top 3 Reminders
1. Fees/slippage can flip paper winners into live losers.
2. Signal quality + token filtering will matter more than most strategy tweaks.
3. Live reliability (confirmation/idempotency/safety gates) matters as much as strategy logic.
