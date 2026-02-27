param(
    [int]$Cycles = 1,
    [int]$PollAttempts = 6,
    [double]$PollIntervalSeconds = 1.0,
    [string]$CycleLogJsonlPath = "data/exports/v14_strategy_cycle_log.jsonl",
    [int]$RequiredFinalizedCycles = 3,
    [int]$MaxCycleRetries = 1,
    [double]$RetryBackoffSeconds = 2.0,
    [string]$ConfigPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json",
    [double]$ExpectedUsdSize = 0.25,
    [bool]$EnforceGuardrailLock = $true,
    [bool]$EnforceRegressionSmoke = $true
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

if ($Cycles -lt 1) { throw "invalid_cycles: must be >= 1" }
if ($MaxCycleRetries -lt 0) { throw "invalid_max_cycle_retries: must be >= 0" }

function Convert-MixedOutputToJsonObject {
    param([string]$Text)
    $raw = [string]$Text
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "json_parse_failed: empty_output"
    }
    $lines = @($raw -split "`r?`n")
    $startIdx = -1
    $endIdx = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($startIdx -lt 0 -and $lines[$i].TrimStart().StartsWith("{")) {
            $startIdx = $i
        }
        if ($lines[$i].TrimEnd().EndsWith("}")) {
            $endIdx = $i
        }
    }
    if ($startIdx -lt 0 -or $endIdx -lt $startIdx) {
        throw "json_parse_failed: no_json_block_found"
    }
    $jsonPart = (($lines[$startIdx..$endIdx]) -join "`n").Trim()
    return ($jsonPart | ConvertFrom-Json)
}

if ($EnforceRegressionSmoke) {
    Write-Host "v14-cycle: regression-smoke preflight check"
    powershell -ExecutionPolicy Bypass -File .\examples\run_v14_regression_smoke.ps1
    if ($LASTEXITCODE -ne 0) { throw "v14_regression_smoke_failed" }
}

if ($EnforceGuardrailLock) {
    Write-Host "v14-cycle: guardrail-lock preflight check"
    powershell -ExecutionPolicy Bypass -File .\examples\check_live_workflow_env_and_config.ps1 -ConfigPath $ConfigPath -ExpectedUsdSize $ExpectedUsdSize
    if ($LASTEXITCODE -ne 0) { throw "v14_guardrail_lock_failed" }
}

function Get-FailureClass {
    param([string]$Msg)
    $m = [string]$Msg
    if ($m -match "HTTP Error 429|Too Many Requests") { return "rpc_429_rate_limited" }
    if ($m -match "confirmation_status=|not_finalized|require_finalized") { return "confirmation_not_finalized" }
    if ($m -match "strategy_demo_full_failed|strategy_decision_trace_failed|strategy_review_packet_failed|strategy_feedback_postprocess_failed") { return "strategy_workflow_failure" }
    if ($m -match "signature_status_failed") { return "signature_status_script_failure" }
    return "unknown_failure"
}

function Append-CycleRow {
    param(
        [hashtable]$Row
    )
    Add-Content -Path $CycleLogJsonlPath -Value ($Row | ConvertTo-Json -Compress)
}

for ($i = 1; $i -le $Cycles; $i++) {
    Write-Host ("v14-cycle: start " + $i + "/" + $Cycles)
    $attempt = 0
    $cycleDone = $false
    while (-not $cycleDone -and $attempt -le $MaxCycleRetries) {
        $attempt += 1
        try {
            powershell -ExecutionPolicy Bypass -File .\examples\run_live_pilot_workflow_preset.ps1 -Preset strategy_demo_full -StrategyAdaptiveFromFeedback
            if ($LASTEXITCODE -ne 0) { throw "strategy_demo_full_failed" }

            $statusRaw = python .\examples\check_latest_live_submit_signature_status.py --require-finalized --poll-attempts $PollAttempts --poll-interval-seconds $PollIntervalSeconds --summary-only
            if ($LASTEXITCODE -ne 0) { throw "signature_status_failed" }

            $statusObj = Convert-MixedOutputToJsonObject -Text $statusRaw
            $confirm = ""
            if ($statusObj.status_summary -and $statusObj.status_summary.confirmation_status) {
                $confirm = [string]$statusObj.status_summary.confirmation_status
            }
            $txPresent = [bool]$statusObj.tx_present
            if (-not $statusObj.ok -or $confirm -ne "finalized" -or -not $txPresent) {
                throw ("cycle_failed_not_finalized: confirmation_status=" + $confirm + ", tx_present=" + $txPresent)
            }

            $row = [ordered]@{
                event_type = "v1_4_strategy_cycle"
                cycle_id = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
                timestamp_utc = [DateTime]::UtcNow.ToString("o")
                ok = [bool]$statusObj.ok
                signature = [string]$statusObj.signature
                confirmation_status = $confirm
                tx_present = $txPresent
                log = [string]$statusObj.log
                slot = $statusObj.status_summary.slot
                attempt = $attempt
                failure_class = ""
                retry_exhausted = $false
            }
            Append-CycleRow -Row $row
            Write-Host ("v14-cycle: finalized signature=" + $row.signature)
            $cycleDone = $true
        } catch {
            $msg = [string]$_.Exception.Message
            $failureClass = Get-FailureClass -Msg $msg
            $retryExhausted = ($attempt -gt $MaxCycleRetries)
            $row = [ordered]@{
                event_type = "v1_4_strategy_cycle"
                cycle_id = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
                timestamp_utc = [DateTime]::UtcNow.ToString("o")
                ok = $false
                signature = ""
                confirmation_status = ""
                tx_present = $false
                log = ""
                slot = $null
                attempt = $attempt
                failure_class = $failureClass
                error_message = $msg
                retry_exhausted = $retryExhausted
            }
            Append-CycleRow -Row $row
            Write-Host ("v14-cycle: attempt failed class=" + $failureClass + " attempt=" + $attempt)
            if ($retryExhausted) {
                throw ("v14_cycle_failed_after_retries: " + $msg)
            }
            Start-Sleep -Seconds $RetryBackoffSeconds
        }
    }
}

python .\examples\export_v14_strategy_cycle_signoff.py `
  --cycle-log-jsonl-path $CycleLogJsonlPath `
  --required-finalized-cycles $RequiredFinalizedCycles `
  --output-json .\data\exports\v14_strategy_cycle_signoff.json `
  --output-md .\data\exports\v14_strategy_cycle_signoff.md
if ($LASTEXITCODE -ne 0) { throw "v14_signoff_export_failed" }

powershell -ExecutionPolicy Bypass -File .\examples\run_v14_ops_bundle_postprocess.ps1
if ($LASTEXITCODE -ne 0) { throw "v14_ops_bundle_postprocess_failed" }

python .\examples\export_v14_release_checkpoint.py `
  --signoff-json-path .\data\exports\v14_strategy_cycle_signoff.json `
  --ops-index-json-path .\data\exports\v14_ops_bundles\index.json `
  --min-bundles 1 `
  --output-json .\data\exports\v14_release_checkpoint.json `
  --output-md .\data\exports\v14_release_checkpoint.md
if ($LASTEXITCODE -ne 0) { throw "v14_release_checkpoint_export_failed" }

Write-Host "v14-cycle: done"
