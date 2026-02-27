param(
    [string]$ExportsDir = "data/exports",
    [string]$V16BundlesDir = "data/exports/v16_bundles",
    [string]$OwnerPubkey = "",
    [switch]$SummaryOnly
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

if (-not (Test-Path $ExportsDir)) {
    throw "exports_dir_not_found: $ExportsDir"
}

Write-Host "v16-postprocess: latest status (polling for finalized if available)"
python .\examples\check_latest_live_submit_signature_status.py --poll-attempts 6 --poll-interval-seconds 1 --require-finalized --summary-only
if ($LASTEXITCODE -ne 0) { throw "v16_status_helper_failed" }

if ($SummaryOnly) { exit 0 }

$receiptArgs = @(".\examples\export_latest_live_submit_receipt.py")
if ($OwnerPubkey) { $receiptArgs += @("--owner-pubkey", $OwnerPubkey) }
$receiptArgs += @("--output-json", (Join-Path $ExportsDir "v16_latest_live_receipt.json"), "--output-md", (Join-Path $ExportsDir "v16_latest_live_receipt.md"))

Write-Host "v16-postprocess: exporting latest receipt"
python @receiptArgs
if ($LASTEXITCODE -ne 0) { throw "v16_receipt_export_failed" }

function Copy-IfExists([string]$From, [string]$To) {
    if (Test-Path $From) {
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent (Join-Path (Get-Location) $To))) | Out-Null
        Copy-Item $From $To -Force
    }
}

$ts = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$bundleName = "v16_run_" + $ts
$bundleDir = Join-Path $V16BundlesDir $bundleName
[System.IO.Directory]::CreateDirectory((Join-Path (Get-Location) $bundleDir)) | Out-Null

Copy-IfExists (Join-Path $ExportsDir "v16_latest_live_receipt.json") (Join-Path $bundleDir "v16_latest_live_receipt.json")
Copy-IfExists (Join-Path $ExportsDir "v16_latest_live_receipt.md") (Join-Path $bundleDir "v16_latest_live_receipt.md")
Copy-IfExists (Join-Path $ExportsDir "v16_discovery_candidates.json") (Join-Path $bundleDir "v16_discovery_candidates.json")
Copy-IfExists (Join-Path $ExportsDir "v16_scored_discovery_report.json") (Join-Path $bundleDir "v16_scored_discovery_report.json")
Copy-IfExists (Join-Path $ExportsDir "v16_scored_discovery_report.md") (Join-Path $bundleDir "v16_scored_discovery_report.md")
Copy-IfExists (Join-Path $ExportsDir "v16_selected_promoted_candidate.json") (Join-Path $bundleDir "v16_selected_promoted_candidate.json")

python .\examples\export_v16_bundles_index.py `
  --v16-bundles-dir $V16BundlesDir `
  --output-json (Join-Path $V16BundlesDir "index.json") `
  --output-md (Join-Path $V16BundlesDir "index.md")
if ($LASTEXITCODE -ne 0) { throw "v16_bundles_index_failed" }

Write-Host ("v16-bundle=" + $bundleDir)
Write-Host "v16-postprocess: done"

