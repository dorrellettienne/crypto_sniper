param(
    [string]$StrategyTraceJsonPath = "data/exports/strategy_decision_trace.json",
    [string]$StrategyTraceLogJsonlPath = "data/exports/strategy_decision_traces.jsonl",
    [string]$TrendSummaryJsonPath = "data/exports/strategy_decision_trace_trend_summary.json",
    [string]$TrendSummaryMdPath = "data/exports/strategy_decision_trace_trend_summary.md",
    [string]$ContextRunLabel = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

if (-not (Test-Path $StrategyTraceJsonPath)) {
    throw "strategy_trace_not_found: $StrategyTraceJsonPath"
}

$tmpPy = "data/exports/_strategy_trace_feedback_tmp.py"
$py = @'
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

def _json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy-trace-json-path", required=True)
    ap.add_argument("--strategy-trace-log-jsonl-path", required=True)
    ap.add_argument("--context-run-label", default="")
    args = ap.parse_args()

    trace_report = _json(args.strategy_trace_json_path)
    payload = {
        "strategy_decision_trace": dict(trace_report.get("strategy_decision_trace") or {}),
        "supervised_entry_promotion_guard": dict(trace_report.get("supervised_entry_promotion_guard") or {}),
        "entry_rule_decisions": dict(trace_report.get("entry_rule_decisions") or {}),
    }
    row = {
        "event_type": "live_pilot_strategy_decision_trace",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "context": {"run_label": str(args.context_run_label or "")},
        "payload": payload,
    }
    p = Path(args.strategy_trace_log_jsonl_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    print(json.dumps({"ok": True, "appended": True, "path": str(p), "event_type": row["event_type"]}, separators=(",", ":")))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'@

[System.IO.Directory]::CreateDirectory((Split-Path -Parent (Join-Path (Get-Location) $tmpPy))) | Out-Null
[System.IO.File]::WriteAllText((Join-Path (Get-Location) $tmpPy), $py, (New-Object System.Text.UTF8Encoding($false)))

try {
    Write-Host "strategy-feedback-postprocess: append strategy trace log"
    python $tmpPy `
      --strategy-trace-json-path $StrategyTraceJsonPath `
      --strategy-trace-log-jsonl-path $StrategyTraceLogJsonlPath `
      --context-run-label $ContextRunLabel
    if ($LASTEXITCODE -ne 0) { throw "strategy_trace_log_append_failed" }

    Write-Host "strategy-feedback-postprocess: export trend summary"
    python .\examples\export_strategy_decision_trace_trend_summary.py `
      --strategy-trace-log-jsonl-path $StrategyTraceLogJsonlPath `
      --output-json $TrendSummaryJsonPath `
      --output-md $TrendSummaryMdPath
    if ($LASTEXITCODE -ne 0) { throw "strategy_trace_trend_export_failed" }
}
finally {
    if (Test-Path $tmpPy) { Remove-Item $tmpPy -ErrorAction SilentlyContinue }
}

Write-Host "strategy-feedback-postprocess: done"
