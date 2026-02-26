param(
    [string]$ExportsDir = "data/exports",
    [string]$RunBundlesDir = "data/exports/run_bundles",
    [string]$OwnerPubkey = "",
    [switch]$SummaryOnly
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

if (-not (Test-Path $ExportsDir)) {
    throw "exports_dir_not_found: $ExportsDir"
}

Write-Host "postprocess: latest status (polling for finalized if available)"
python .\examples\check_latest_live_submit_signature_status.py --poll-attempts 6 --poll-interval-seconds 1 --require-finalized --summary-only
if ($LASTEXITCODE -ne 0) { throw "status_helper_failed" }

if ($SummaryOnly) {
    exit 0
}

$receiptArgs = @(".\examples\export_latest_live_submit_receipt.py")
if ($OwnerPubkey) {
    $receiptArgs += @("--owner-pubkey", $OwnerPubkey)
}
$receiptArgs += @("--output-json", (Join-Path $ExportsDir "latest_live_receipt.json"), "--output-md", (Join-Path $ExportsDir "latest_live_receipt.md"))

Write-Host "postprocess: exporting latest receipt"
python @receiptArgs
if ($LASTEXITCODE -ne 0) { throw "receipt_export_failed" }

$packetArgs = @(
    ".\examples\export_daily_live_validation_packet.py",
    "--exports-dir", $ExportsDir,
    "--output-json", (Join-Path $ExportsDir "daily_live_validation_packet.json"),
    "--output-md", (Join-Path $ExportsDir "daily_live_validation_packet.md"),
    "--pack-dir", $RunBundlesDir
)
if ($OwnerPubkey) {
    $packetArgs += @("--owner-pubkey", $OwnerPubkey)
}

Write-Host "postprocess: exporting daily packet + bundle pack"
python @packetArgs
$packetExportOk = ($LASTEXITCODE -eq 0)
if (-not $packetExportOk) {
    Write-Warning "daily_packet_export_failed (continuing with partial postprocess; likely RPC rate limit)"
}

Write-Host "postprocess: refreshing run bundle index"
python .\examples\export_run_bundles_index.py --run-bundles-dir $RunBundlesDir --output-json (Join-Path $RunBundlesDir "index.json") --output-md (Join-Path $RunBundlesDir "index.md")
if ($LASTEXITCODE -ne 0) { throw "run_bundles_index_export_failed" }

if ($packetExportOk) {
    Write-Host "postprocess: done"
    exit 0
}

Write-Host "postprocess: partial_success (status+receipt+index ok; daily packet export failed)"
exit 0
