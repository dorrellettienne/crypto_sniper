param(
    [ValidateSet("tiny_live_usdc", "no_send_usdc", "status_only", "scored_discovery_demo", "strategy_demo", "strategy_demo_full", "v14_strategy_cycle", "v14_release_checkpoint")]
    [string]$Preset = "status_only",
    [double]$UsdSize = 0.25,
    [string]$ConfigPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json",
    [string]$NoSendConfigPath = "config/live_pilot_solana_no_send_local.json",
    [int]$PollIntervalSeconds = 1,
    [string]$ScoredDiscoveryCandidateJsonPath = "examples/live_pilot_candidate_list_dexscreener_scored_demo.json",
    [switch]$StrategyAdaptiveFromFeedback,
    [switch]$StrategyAdaptiveExitPolicyFromFeedback
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

function Invoke-StrategyDemo {
    if (-not (Test-Path $ScoredDiscoveryCandidateJsonPath)) {
        throw "candidate_json_not_found: $ScoredDiscoveryCandidateJsonPath"
    }
    $env:PYTHONPATH = "."
    $args = @(
        ".\examples\export_strategy_decision_trace.py",
        "--candidate-json-path", $ScoredDiscoveryCandidateJsonPath,
        "--output-json", ".\data\exports\strategy_decision_trace.json",
        "--output-md", ".\data\exports\strategy_decision_trace.md",
        "--entry-require-probe-ok",
        "--guard-require-confidence-at-least", "medium"
    )
    if ($StrategyAdaptiveFromFeedback) {
        $args += "--entry-adaptive-from-feedback"
    }
    if ($StrategyAdaptiveExitPolicyFromFeedback -or $StrategyAdaptiveFromFeedback) {
        $args += "--exit-policy-adaptive-from-feedback"
    }
    python @args
    if ($LASTEXITCODE -ne 0) {
        throw "strategy_decision_trace_failed"
    }
    Write-Host "strategy_decision_trace_json=data/exports/strategy_decision_trace.json"
    Write-Host "strategy_decision_trace_md=data/exports/strategy_decision_trace.md"
}

function Invoke-StrategyDemoFull {
    Invoke-StrategyDemo
    if ($LASTEXITCODE -ne 0) {
        throw "strategy_demo_failed"
    }
    powershell -ExecutionPolicy Bypass -File .\examples\run_strategy_decision_feedback_postprocess.ps1 -ContextRunLabel "strategy_demo_full"
    if ($LASTEXITCODE -ne 0) {
        throw "strategy_feedback_postprocess_failed"
    }
    $env:PYTHONPATH = "."
    python .\examples\export_strategy_decision_review_packet.py `
        --strategy-trace-json-path .\data\exports\strategy_decision_trace.json `
        --strategy-trend-json-path .\data\exports\strategy_decision_trace_trend_summary.json `
        --scored-discovery-json-path .\data\exports\scored_discovery_report.json `
        --calibration-trend-json-path .\data\exports\scored_candidate_calibration_trend_summary.json `
        --output-json .\data\exports\strategy_decision_review_packet.json `
        --output-md .\data\exports\strategy_decision_review_packet.md `
        --context-run-label strategy_demo_full
    if ($LASTEXITCODE -ne 0) {
        throw "strategy_review_packet_failed"
    }
    powershell -ExecutionPolicy Bypass -File .\examples\run_strategy_review_packet_postprocess.ps1
    if ($LASTEXITCODE -ne 0) {
        throw "strategy_review_packet_postprocess_failed"
    }
    Write-Host "strategy_decision_review_packet_json=data/exports/strategy_decision_review_packet.json"
    Write-Host "strategy_decision_review_packet_md=data/exports/strategy_decision_review_packet.md"
}

function Invoke-V14StrategyCycle {
    powershell -ExecutionPolicy Bypass -File .\examples\run_v14_strategy_cycle.ps1 -Cycles 1 -PollAttempts 6 -PollIntervalSeconds 1.0
    if ($LASTEXITCODE -ne 0) {
        throw "v14_strategy_cycle_failed"
    }
}

function Invoke-V14ReleaseCheckpoint {
    $env:PYTHONPATH = "."
    python .\examples\export_v14_release_checkpoint.py `
      --signoff-json-path .\data\exports\v14_strategy_cycle_signoff.json `
      --ops-index-json-path .\data\exports\v14_ops_bundles\index.json `
      --min-bundles 1 `
      --output-json .\data\exports\v14_release_checkpoint.json `
      --output-md .\data\exports\v14_release_checkpoint.md
    if ($LASTEXITCODE -ne 0) {
        throw "v14_release_checkpoint_failed"
    }
}

Write-Host ("preset=" + $Preset)
switch ($Preset) {
    "tiny_live_usdc" { Invoke-TinyLiveUsdc; break }
    "no_send_usdc" { Invoke-NoSendUsdc; break }
    "status_only" { Invoke-StatusOnly; break }
    "scored_discovery_demo" { Invoke-ScoredDiscoveryDemo; break }
    "strategy_demo" { Invoke-StrategyDemo; break }
    "strategy_demo_full" { Invoke-StrategyDemoFull; break }
    "v14_strategy_cycle" { Invoke-V14StrategyCycle; break }
    "v14_release_checkpoint" { Invoke-V14ReleaseCheckpoint; break }
}
