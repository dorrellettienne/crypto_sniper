param(
    [ValidateSet("tiny_live_usdc", "no_send_usdc", "status_only")]
    [string]$Preset = "status_only",
    [double]$UsdSize = 0.25,
    [string]$ConfigPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json",
    [string]$NoSendConfigPath = "config/live_pilot_solana_no_send_local.json",
    [int]$PollIntervalSeconds = 1
)

$ErrorActionPreference = "Stop"

function Invoke-StatusOnly {
    python .\examples\check_latest_live_submit_signature_status.py
}

function Invoke-TinyLiveUsdc {
    if (-not $env:SOLANA_PILOT_PRIVATE_KEY_B58) {
        throw "missing_env: SOLANA_PILOT_PRIVATE_KEY_B58"
    }
    $args = @(
        "-ExecutionPolicy", "Bypass",
        "-File", ".\examples\run_tiny_live_usdc_workflow.ps1",
        "-ConfigPath", $ConfigPath,
        "-UsdSize", ([string]$UsdSize),
        "-PollIntervalSeconds", ([string]$PollIntervalSeconds)
    )
    powershell @args
}

function Invoke-NoSendUsdc {
    if (-not (Test-Path $NoSendConfigPath)) {
        throw "config_not_found: $NoSendConfigPath"
    }
    $env:PYTHONPATH = "."
    python -m src.live.live_pilot_service `
        --mode live_auto_tiny_one_trade `
        --token-address EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v `
        --symbol USDC `
        --entry-price 1 `
        --usd-size $UsdSize `
        --allow-unsafe-paths `
        --adapter-config-json-path $NoSendConfigPath `
        --print-human-summary
}

Write-Host ("preset=" + $Preset)
switch ($Preset) {
    "tiny_live_usdc" { Invoke-TinyLiveUsdc; break }
    "no_send_usdc" { Invoke-NoSendUsdc; break }
    "status_only" { Invoke-StatusOnly; break }
}
