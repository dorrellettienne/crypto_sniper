param(
    [string]$V14CycleLogJsonlPath = "data/exports/v14_strategy_cycle_log.jsonl",
    [string]$V14SignoffJsonPath = "data/exports/v14_strategy_cycle_signoff.json",
    [string]$V14SignoffMdPath = "data/exports/v14_strategy_cycle_signoff.md",
    [string]$StrategyReviewPacketJsonPath = "data/exports/strategy_decision_review_packet.json",
    [string]$StrategyReviewPacketMdPath = "data/exports/strategy_decision_review_packet.md",
    [string]$V14OpsBundlesDir = "data/exports/v14_ops_bundles"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

function Copy-IfExists([string]$From, [string]$To) {
    if (Test-Path $From) {
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent (Join-Path (Get-Location) $To))) | Out-Null
        Copy-Item $From $To -Force
    }
}

$ts = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$bundleName = "v14_ops_" + $ts
$bundleDir = Join-Path $V14OpsBundlesDir $bundleName
[System.IO.Directory]::CreateDirectory((Join-Path (Get-Location) $bundleDir)) | Out-Null

Copy-IfExists $V14CycleLogJsonlPath (Join-Path $bundleDir "v14_strategy_cycle_log.jsonl")
Copy-IfExists $V14SignoffJsonPath (Join-Path $bundleDir "v14_strategy_cycle_signoff.json")
Copy-IfExists $V14SignoffMdPath (Join-Path $bundleDir "v14_strategy_cycle_signoff.md")
Copy-IfExists $StrategyReviewPacketJsonPath (Join-Path $bundleDir "strategy_decision_review_packet.json")
Copy-IfExists $StrategyReviewPacketMdPath (Join-Path $bundleDir "strategy_decision_review_packet.md")

python .\examples\export_v14_ops_bundles_index.py `
  --v14-ops-bundles-dir $V14OpsBundlesDir `
  --output-json (Join-Path $V14OpsBundlesDir "index.json") `
  --output-md (Join-Path $V14OpsBundlesDir "index.md")
if ($LASTEXITCODE -ne 0) {
    throw "v14_ops_bundles_index_failed"
}

Write-Host ("v14-ops-bundle=" + $bundleDir)
Write-Host "v14-ops-postprocess: done"

