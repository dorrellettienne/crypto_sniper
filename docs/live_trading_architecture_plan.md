# Live Trading Architecture Plan (Post Paper-Mode V1)

## Goal
Prepare a safe path from paper-mode v1 to live-trading architecture without mixing live execution concerns into the current paper-mode codebase.

## Principles
- Keep paper and live execution adapters separate.
- Preserve existing risk engine behavior where possible.
- Make observability/logging first-class before any live order placement.
- Roll out in stages with kill-switch support.

## Proposed Components

### 1. Signal Layer
- Responsibility: produce trade intents (`BUY`, `SELL`, `HOLD`) from strategy logic.
- Output should be a structured signal object, not direct exchange calls.
- Should be testable with deterministic fixtures.

### 2. Risk Engine (Shared)
- Responsibility: approve/reject signals before execution.
- Reuse current risk rules conceptually:
  - max daily loss
  - max concurrent positions
  - optional symbol/token filters
- Expose a pure decision API where possible.

### 3. Position Manager (Shared Core)
- Responsibility: track position lifecycle state transitions.
- For paper mode: persists to SQLite.
- For live mode: persists to SQLite + tracks broker/exchange order IDs.

### 4. Execution Adapter (Environment-Specific)
- `PaperExecutionAdapter`
  - wraps current simulated execution functions
- `LiveExecutionAdapter` (future)
  - places/cancels orders
  - confirms fills
  - retries with strict safety limits

### 5. Market Data Adapter
- Responsibility: provide price/quote/market state to strategy and risk engine.
- Separate from execution adapter to avoid tight coupling.

### 6. Runner / Orchestrator
- Responsibility: polling loop, scheduling, and pipeline sequencing
  - fetch market data
  - generate signal
  - risk check
  - execution
  - persistence
  - reporting/export

## Interface Direction (High Level)
- Strategy emits intent -> Risk validates -> Execution adapter executes -> Persistence records -> Reporting/export summarizes.

## Safety Controls for Live Phase
- Global kill switch
- Paper/live mode explicit startup confirmation
- Max notional per trade
- Max daily order count
- Dry-run logging mode before live enablement
- Audit log of all decisions (signal/risk/execution outcome)

## Migration Plan (Incremental)
1. Extract runner orchestration interfaces (no behavior change).
2. Introduce paper execution adapter wrapper around existing `paper_engine`.
3. Define signal and risk decision data contracts.
4. Add market data adapter abstraction.
5. Implement live adapter in disabled/dry-run mode.
6. End-to-end dry-run validation with full logging.
7. Limited live pilot with strict caps.

## Non-Goals (for now)
- High-frequency execution
- Multi-exchange support
- Complex portfolio optimization
- Real-time web dashboard backend

## Current Readiness Summary
- Paper-mode persistence/reporting: strong
- Runner/export pipeline: strong enough for v1
- Risk logic: usable and test-covered, but not yet abstracted for live adapters
- Next best engineering step: adapter boundaries, not live execution code
