param(
    [string]$V14CycleLogJsonlPath = "data/exports/v14_strategy_cycle_log.jsonl",
    [string]$V14ReleaseCheckpointJsonPath = "data/exports/v14_release_checkpoint.json",
    [string]$V14ReleaseCheckpointMdPath = "data/exports/v14_release_checkpoint.md",
    [string]$V15EntryReadinessJsonPath = "data/exports/v15_entry_readiness.json",
    [string]$V15EntryReadinessMdPath = "data/exports/v15_entry_readiness.md",
    [string]$V15ReleaseCheckpointJsonPath = "data/exports/v15_release_checkpoint.json",
    [string]$V15ReleaseCheckpointMdPath = "data/exports/v15_release_checkpoint.md",
    [string]$V15OpsBundlesDir = "data/exports/v15_ops_bundles"
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
$bundleName = "v15_ops_" + $ts
$bundleDir = Join-Path $V15OpsBundlesDir $bundleName
[System.IO.Directory]::CreateDirectory((Join-Path (Get-Location) $bundleDir)) | Out-Null

Copy-IfExists $V14CycleLogJsonlPath (Join-Path $bundleDir "v14_strategy_cycle_log.jsonl")
Copy-IfExists $V14ReleaseCheckpointJsonPath (Join-Path $bundleDir "v14_release_checkpoint.json")
Copy-IfExists $V14ReleaseCheckpointMdPath (Join-Path $bundleDir "v14_release_checkpoint.md")
Copy-IfExists $V15EntryReadinessJsonPath (Join-Path $bundleDir "v15_entry_readiness.json")
Copy-IfExists $V15EntryReadinessMdPath (Join-Path $bundleDir "v15_entry_readiness.md")
Copy-IfExists $V15ReleaseCheckpointJsonPath (Join-Path $bundleDir "v15_release_checkpoint.json")
Copy-IfExists $V15ReleaseCheckpointMdPath (Join-Path $bundleDir "v15_release_checkpoint.md")

python .\examples\export_v15_ops_bundles_index.py `
  --v15-ops-bundles-dir $V15OpsBundlesDir `
  --output-json (Join-Path $V15OpsBundlesDir "index.json") `
  --output-md (Join-Path $V15OpsBundlesDir "index.md")
if ($LASTEXITCODE -ne 0) {
    throw "v15_ops_bundles_index_failed"
}

Write-Host ("v15-ops-bundle=" + $bundleDir)
Write-Host "v15-ops-postprocess: done"

