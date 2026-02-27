param(
    [string]$ConfigPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json",
    [double]$UsdSize = 0.25,
    [int]$PollIntervalSeconds = 1,
    [string]$DexscreenerFetchUrl = "https://api.dexscreener.com/latest/dex/search/?q=solana%20pump",
    [string]$DexscreenerFallbackUrlsJsonPath = "examples/dexscreener_fallback_urls_demo.json",
    [double]$MinLiquidityUsd = 25000.0,
    [double]$MaxPairAgeSeconds = 900.0,
    [double]$MinVolume5mUsd = 6000.0,
    [double]$MaxAbsPriceChange5mPct = 18.0,
    [double]$PromoteMinScoreTotal = 30.0,
    [int]$PromoteMaxCandidates = 1,
    [switch]$PromoteRequireProbeOk,
    [int]$MaxFetchedCandidates = 120,
    [string]$DexscreenerUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    [switch]$SkipStatusCheck
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

if (-not (Test-Path $ConfigPath)) {
    throw "config_not_found: $ConfigPath"
}
if (-not $env:SOLANA_PILOT_PRIVATE_KEY_B58) {
    throw "missing_env: SOLANA_PILOT_PRIVATE_KEY_B58"
}

$pubOut = python .\examples\print_solana_signer_pubkey_from_env.py
if (-not $pubOut) { throw "signer_pubkey_helper_no_output" }
$pub = $pubOut | ConvertFrom-Json
if (-not $pub.ok) { throw ("signer_pubkey_helper_failed: " + ($pub.reason | Out-String)) }
$signerPubkey = [string]$pub.pubkey

$cfg = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$cfg.wallet_public_key = $signerPubkey
$cfg.live_send_network_enabled = $true
$cfg.dex_quote_only_mode = $false
$cfg.max_order_usd_cap = [double]$UsdSize
$cfg.pilot_hard_max_order_usd_cap = [double]$UsdSize
$cfg.live_send_max_notional_usd_total = [double]$UsdSize
$cfg.live_send_max_orders_per_session = 1
$cfg | Add-Member NoteProperty live_send_chain_reconciliation_fetch_attempts 12 -Force
$cfg | Add-Member NoteProperty live_send_chain_reconciliation_fetch_poll_interval_seconds 1.5 -Force
$txt = $cfg | ConvertTo-Json -Depth 12
[System.IO.File]::WriteAllText((Resolve-Path $ConfigPath), $txt, (New-Object System.Text.UTF8Encoding($false)))

$preflightJson = powershell -ExecutionPolicy Bypass -File .\examples\check_live_workflow_env_and_config.ps1 -ConfigPath $ConfigPath -ExpectedUsdSize $UsdSize
if (-not $preflightJson) { throw "env_preflight_no_output" }
$preflight = $preflightJson | ConvertFrom-Json
if (-not $preflight.ok) { throw ("env_preflight_failed: " + ($preflightJson | Out-String)) }

$candidateJsonPath = ".\data\exports\v16_discovery_candidates.json"
$scoredJsonPath = ".\data\exports\v16_scored_discovery_report.json"
$scoredMdPath = ".\data\exports\v16_scored_discovery_report.md"
$selectionJsonPath = ".\data\exports\v16_selected_promoted_candidate.json"

python .\examples\export_dexscreener_candidates.py `
  --fetch-url $DexscreenerFetchUrl `
  --fallback-urls-json-path $DexscreenerFallbackUrlsJsonPath `
  --user-agent $DexscreenerUserAgent `
  --chain-id solana `
  --usd-size ([string]$UsdSize) `
  --max-candidates ([string]$MaxFetchedCandidates) `
  --output-json $candidateJsonPath
if ($LASTEXITCODE -ne 0) { throw "v16_dexscreener_candidate_export_failed" }

$scoreArgs = @(
    ".\examples\export_scored_discovery_report.py",
    "--candidate-json-path", $candidateJsonPath,
    "--output-json", $scoredJsonPath,
    "--output-md", $scoredMdPath,
    "--min-liquidity-usd", ([string]$MinLiquidityUsd),
    "--max-pair-age-seconds", ([string]$MaxPairAgeSeconds),
    "--min-volume-5m-usd", ([string]$MinVolume5mUsd),
    "--max-abs-price-change-5m-pct", ([string]$MaxAbsPriceChange5mPct),
    "--promote-max-candidates", ([string]$PromoteMaxCandidates),
    "--promote-min-score-total", ([string]$PromoteMinScoreTotal)
)
if ($PromoteRequireProbeOk) { $scoreArgs += "--promote-require-probe-ok" }
python @scoreArgs
if ($LASTEXITCODE -ne 0) { throw "v16_scored_discovery_failed" }

$scored = Get-Content -Raw $scoredJsonPath | ConvertFrom-Json
$promoted = @()
if ($scored.promotion -and $scored.promotion.promoted_candidates) {
    $promoted = @($scored.promotion.promoted_candidates)
}
if ($promoted.Count -lt 1) {
    throw "v16_no_promoted_candidates_after_scoring"
}

$selected = $promoted[0]
$tokenAddress = [string]$selected.token_address
$symbol = [string]$selected.symbol
$entryPrice = [double]$selected.features.entry_price
if (-not $entryPrice -or $entryPrice -le 0) { $entryPrice = 1.0 }

$selected | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 $selectionJsonPath

Write-Host ("signer_pubkey=" + $signerPubkey)
Write-Host ("selected_token=" + $tokenAddress)
Write-Host ("selected_symbol=" + $symbol)
Write-Host ("selected_entry_price=" + $entryPrice)
Write-Host ("selected_score_total=" + ([string]$selected.score_total))
Write-Host ("scored_report=" + (Resolve-Path $scoredJsonPath))

$args = @(
    "-m", "src.live.live_pilot_service",
    "--mode", "live_auto_tiny_one_trade",
    "--token-address", $tokenAddress,
    "--symbol", $symbol,
    "--entry-price", ([string]$entryPrice),
    "--usd-size", ([string]$UsdSize),
    "--allow-unsafe-paths",
    "--adapter-config-json-path", $ConfigPath,
    "--auto-pilot-poll-interval-seconds", ([string]$PollIntervalSeconds),
    "--print-human-summary"
)
python @args
$exitCode = $LASTEXITCODE

if (-not $SkipStatusCheck) {
    Write-Host ""
    Write-Host "latest_submit_status:"
    python .\examples\check_latest_live_submit_signature_status.py
}

exit $exitCode
