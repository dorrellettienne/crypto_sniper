param(
    [int]$MinCyclesTotal = 7,
    [int]$RecentCyclesRequired = 3
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

python .\examples\export_v15_entry_readiness.py `
  --release-checkpoint-json-path .\data\exports\v14_release_checkpoint.json `
  --cycle-log-jsonl-path .\data\exports\v14_strategy_cycle_log.jsonl `
  --min-cycles-total $MinCyclesTotal `
  --recent-cycles-required $RecentCyclesRequired `
  --output-json .\data\exports\v15_entry_readiness.json `
  --output-md .\data\exports\v15_entry_readiness.md
if ($LASTEXITCODE -ne 0) { throw "v15_entry_readiness_export_failed" }

$report = Get-Content -Raw .\data\exports\v15_entry_readiness.json | ConvertFrom-Json
if (-not $report.entry_ready) {
    throw ("v15_entry_gate_blocked:" + [string]$report.summary)
}

Write-Host "v15-entry-gate: entry_ready=true"

