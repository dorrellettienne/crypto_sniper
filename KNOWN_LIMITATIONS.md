# Known Limitations (V1 Supervised Tiny Live Pilot)

This project version is a supervised tiny live pilot, not a production unattended trading system.

## Operational Limits

- Operator supervision is required for all live runs
- Intended for tiny test size (for example `$0.25`) only
- Not approved for unattended or high-frequency live operation
- Wallet/env setup is session-scoped (`SOLANA_PILOT_PRIVATE_KEY_B58` must be loaded per PowerShell session)

## Reconciliation / Confirmation Limits

- Reconciliation can return `confirmed_only` within the polling window (not always `finalized` immediately)
- RPC timing/rate limits may still cause intermittent inconclusive or delayed confirmation visibility
- Finality truth should be cross-checked with:
  - `examples/check_latest_live_submit_signature_status.py`
  - `examples/export_latest_live_submit_receipt.py`

## Economics / Mismatch Interpretation

- Quote vs settlement mismatch can be influenced by setup overhead (ATA create/close, wrap/unwrap effects)
- `quote_vs_settlement_mismatch` is classified, but not all causes are strategy slippage
- Fee totals may include setup/rent-related effects, not only base tx fee

## Strategy Scope

- Current validation flow is focused on proving execution/reconciliation plumbing
- “Can trade” is proven more than “profitable strategy selection”
- Discovery/provider quality and token selection remain external risk factors

## External Dependency Risks

- RPC rate limiting (`HTTP 429`) can disrupt runs
- Jupiter quote/swap APIs may time out or return route-specific failures
- Third-party providers (e.g., DexScreener) may return `403`/availability issues

## Security / Wallet Handling

- Base58 private key handling in terminal sessions is sensitive and operator-managed
- Dedicated pilot wallet usage is strongly recommended
- Do not use the same workflow/scripts with a primary treasury wallet

## UX / Tooling Limits

- PowerShell paste/line-wrap behavior can still cause command formatting errors if not using scripts
- Some advanced auth/guard artifacts exist but are beyond V1 release scope for daily use

## Non-Goals For V1

- Unattended autonomous sniping
- Production-grade monitoring/alerting stack
- Multi-region RPC redundancy and failover automation
- Capital scaling or frequency scaling beyond supervised tiny pilots

