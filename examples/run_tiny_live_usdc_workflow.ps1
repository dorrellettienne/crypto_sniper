param(
    [string]$ConfigPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json",
    [double]$UsdSize = 0.25,
    [int]$PollIntervalSeconds = 1,
    [string]$TokenAddress = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    [string]$Symbol = "USDC",
    [int]$MinCooldownSeconds = 90,
    [string]$CooldownStatePath = "data/exports/live_tiny_workflow_cooldown_state.json",
    [switch]$SkipStatusCheck
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ConfigPath)) {
    throw "config_not_found: $ConfigPath"
}

if (-not $env:SOLANA_PILOT_PRIVATE_KEY_B58) {
    throw "missing_env: SOLANA_PILOT_PRIVATE_KEY_B58"
}

$env:PYTHONPATH = "."

# S4-M109: prevent accidental rapid repeat submissions / RPC spam.
if ($MinCooldownSeconds -gt 0) {
    $nowUnix = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if (Test-Path $CooldownStatePath) {
        try {
            $cool = Get-Content -Raw $CooldownStatePath | ConvertFrom-Json
            $lastRunUnix = [int]($cool.last_run_started_unix -as [int])
            if ($lastRunUnix -gt 0) {
                $elapsed = $nowUnix - $lastRunUnix
                if ($elapsed -lt $MinCooldownSeconds) {
                    throw ("cooldown_active: wait_" + ($MinCooldownSeconds - $elapsed) + "s (min=" + $MinCooldownSeconds + "s)")
                }
            }
        } catch {
            # ignore malformed cooldown state and continue
        }
    }
    $coolState = @{ last_run_started_unix = $nowUnix; min_cooldown_seconds = $MinCooldownSeconds }
    $coolTxt = $coolState | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) $CooldownStatePath), $coolTxt, (New-Object System.Text.UTF8Encoding($false)))
}

$pubOut = python examples/print_solana_signer_pubkey_from_env.py
if (-not $pubOut) {
    throw "signer_pubkey_helper_no_output"
}
$pub = $pubOut | ConvertFrom-Json
if (-not $pub.ok) {
    throw ("signer_pubkey_helper_failed: " + ($pub.reason | Out-String))
}
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

# S4-M108: env/config preflight check after syncing signer pubkey and tiny caps.
$preflightJson = powershell -ExecutionPolicy Bypass -File .\examples\check_live_workflow_env_and_config.ps1 -ConfigPath $ConfigPath -ExpectedUsdSize $UsdSize
if (-not $preflightJson) {
    throw "env_preflight_no_output"
}
$preflight = $preflightJson | ConvertFrom-Json
if (-not $preflight.ok) {
    throw ("env_preflight_failed: " + ($preflightJson | Out-String))
}

Write-Host ("signer_pubkey=" + $signerPubkey)
Write-Host ("config_synced=" + (Resolve-Path $ConfigPath))
Write-Host ("usd_size=" + $UsdSize)
Write-Host ("cooldown_min_seconds=" + $MinCooldownSeconds)

$args = @(
    "-m", "src.live.live_pilot_service",
    "--mode", "live_auto_tiny_one_trade",
    "--token-address", $TokenAddress,
    "--symbol", $Symbol,
    "--entry-price", "1",
    "--usd-size", ([string]$UsdSize),
    "--allow-unsafe-paths",
    "--adapter-config-json-path", $ConfigPath,
    "--auto-pilot-poll-interval-seconds", ([string]$PollIntervalSeconds),
    "--print-human-summary"
)

python @args
$exitCode = $LASTEXITCODE

if ($SkipStatusCheck) {
    exit $exitCode
}

Write-Host ""
Write-Host "latest_submit_status:"
python examples/check_latest_live_submit_signature_status.py

exit $exitCode
