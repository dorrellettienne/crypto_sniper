param(
    [ValidateSet("tiny_live_usdc", "no_send_usdc", "status_only", "scored_discovery_demo")]
    [string]$Preset = "status_only",
    [double]$UsdSize = 0.25,
    [string]$ConfigPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json",
    [string]$NoSendConfigPath = "config/live_pilot_solana_no_send_local.json",
    [int]$PollIntervalSeconds = 1,
    [string]$ScoredDiscoveryCandidateJsonPath = "examples/live_pilot_candidate_list_dexscreener_scored_demo.json"
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

function Invoke-ScoredDiscoveryDemo {
    if (-not (Test-Path $ScoredDiscoveryCandidateJsonPath)) {
        throw "candidate_json_not_found: $ScoredDiscoveryCandidateJsonPath"
    }
    $env:PYTHONPATH = "."
    python .\examples\export_scored_discovery_report.py `
        --candidate-json-path $ScoredDiscoveryCandidateJsonPath `
        --output-json .\data\exports\scored_discovery_report.json `
        --output-md .\data\exports\scored_discovery_report.md
    if ($LASTEXITCODE -ne 0) {
        throw "scored_discovery_report_failed"
    }
    Write-Host "scored_discovery_report_json=data/exports/scored_discovery_report.json"
    Write-Host "scored_discovery_report_md=data/exports/scored_discovery_report.md"
}

Write-Host ("preset=" + $Preset)
switch ($Preset) {
    "tiny_live_usdc" { Invoke-TinyLiveUsdc; break }
    "no_send_usdc" { Invoke-NoSendUsdc; break }
    "status_only" { Invoke-StatusOnly; break }
    "scored_discovery_demo" { Invoke-ScoredDiscoveryDemo; break }
}
