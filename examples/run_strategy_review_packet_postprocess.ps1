param(
    [string]$ReviewPacketJsonPath = "data/exports/strategy_decision_review_packet.json",
    [string]$ReviewPacketMdPath = "data/exports/strategy_decision_review_packet.md",
    [string]$StrategyTraceJsonPath = "data/exports/strategy_decision_trace.json",
    [string]$StrategyTraceMdPath = "data/exports/strategy_decision_trace.md",
    [string]$StrategyTrendJsonPath = "data/exports/strategy_decision_trace_trend_summary.json",
    [string]$StrategyTrendMdPath = "data/exports/strategy_decision_trace_trend_summary.md",
    [string]$ScoredDiscoveryJsonPath = "data/exports/scored_discovery_report.json",
    [string]$ScoredDiscoveryMdPath = "data/exports/scored_discovery_report.md",
    [string]$CalibrationTrendJsonPath = "data/exports/scored_candidate_calibration_trend_summary.json",
    [string]$CalibrationTrendMdPath = "data/exports/scored_candidate_calibration_trend_summary.md",
    [string]$StrategyBundlesDir = "data/exports/strategy_bundles"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

if (-not (Test-Path $ReviewPacketJsonPath)) {
    throw "strategy_review_packet_not_found: $ReviewPacketJsonPath"
}

function Copy-IfExists([string]$From, [string]$To) {
    if (Test-Path $From) {
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent (Join-Path (Get-Location) $To))) | Out-Null
        Copy-Item $From $To -Force
    }
}

$ts = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$bundleName = "strategy_review_" + $ts
$bundleDir = Join-Path $StrategyBundlesDir $bundleName
[System.IO.Directory]::CreateDirectory((Join-Path (Get-Location) $bundleDir)) | Out-Null

Copy-IfExists $ReviewPacketJsonPath (Join-Path $bundleDir "strategy_decision_review_packet.json")
Copy-IfExists $ReviewPacketMdPath (Join-Path $bundleDir "strategy_decision_review_packet.md")
Copy-IfExists $StrategyTraceJsonPath (Join-Path $bundleDir "strategy_decision_trace.json")
Copy-IfExists $StrategyTraceMdPath (Join-Path $bundleDir "strategy_decision_trace.md")
Copy-IfExists $StrategyTrendJsonPath (Join-Path $bundleDir "strategy_decision_trace_trend_summary.json")
Copy-IfExists $StrategyTrendMdPath (Join-Path $bundleDir "strategy_decision_trace_trend_summary.md")
Copy-IfExists $ScoredDiscoveryJsonPath (Join-Path $bundleDir "scored_discovery_report.json")
Copy-IfExists $ScoredDiscoveryMdPath (Join-Path $bundleDir "scored_discovery_report.md")
Copy-IfExists $CalibrationTrendJsonPath (Join-Path $bundleDir "scored_candidate_calibration_trend_summary.json")
Copy-IfExists $CalibrationTrendMdPath (Join-Path $bundleDir "scored_candidate_calibration_trend_summary.md")

python .\examples\export_strategy_review_bundles_index.py `
  --strategy-bundles-dir $StrategyBundlesDir `
  --output-json (Join-Path $StrategyBundlesDir "index.json") `
  --output-md (Join-Path $StrategyBundlesDir "index.md")
if ($LASTEXITCODE -ne 0) {
    throw "strategy_review_bundles_index_failed"
}

Write-Host ("strategy-review-bundle=" + $bundleDir)
Write-Host "strategy-review-postprocess: done"
