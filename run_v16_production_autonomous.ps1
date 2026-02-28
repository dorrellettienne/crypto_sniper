param(
    [string]$RepoPath = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string]$KeyFilePath = "C:\secure\pilot\pilot_keypair.json",
    [string]$AlertWebhookUrl = "",
    [switch]$RelaxedTest,
    [switch]$EnableAutoExit,
    [double]$AutoExitPriceMultiplier = 1.01,
    [double]$AutoExitDelaySeconds = 0.0,
    [int]$AutoExitSellWaitTimeoutSeconds = 45,
    [int]$ReleaseCycleTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
Set-Location $RepoPath
$env:PYTHONPATH = "."
$runStartUtc = [DateTime]::UtcNow

function Set-SignerEnv {
    $helper = Join-Path $RepoPath "examples\print_solana_signer_pubkey_from_env.py"
    if (($env:SOLANA_PILOT_PRIVATE_KEY_B58 -or $env:SOLANA_PILOT_KEYPAIR_JSON_PATH) -and (Test-Path $helper)) {
        python $helper | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Remove-Item Env:SOLANA_PILOT_PRIVATE_KEY_B58 -ErrorAction SilentlyContinue
        Remove-Item Env:SOLANA_PILOT_KEYPAIR_JSON_PATH -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path $KeyFilePath)) {
        throw "missing_signer_secret_env_and_key_file_not_found: $KeyFilePath"
    }
    $raw = (Get-Content $KeyFilePath -Raw).Trim()
    if (-not $raw) {
        throw "key_file_empty: $KeyFilePath"
    }
    if ($raw.StartsWith("[")) {
        $env:SOLANA_PILOT_KEYPAIR_JSON_PATH = $KeyFilePath
    } else {
        $env:SOLANA_PILOT_PRIVATE_KEY_B58 = $raw
    }
}

function Read-Json([string]$Path) {
    if (-not (Test-Path $Path)) {
        throw "required_artifact_missing: $Path"
    }
    return Get-Content $Path -Raw | ConvertFrom-Json
}

function Try-ReadJson([string]$Path) {
    if (-not (Test-Path $Path)) {
        return $null
    }
    try {
        return Get-Content $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Configure-AutoExitIfEnabled {
    if (-not $EnableAutoExit) {
        return
    }
    $configPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json"
    if (-not (Test-Path $configPath)) {
        throw "auto_exit_config_missing: $configPath"
    }
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    $cfg.manual_submit_mode = "buy_and_sell"
    $cfg.live_auto_exit_enabled = $true
    $cfg.live_auto_exit_price_multiplier = [double]$AutoExitPriceMultiplier
    $cfg.live_auto_exit_delay_seconds = [double]$AutoExitDelaySeconds
    $cfg.live_send_max_orders_per_session = 2
    $cfg.live_send_max_notional_usd_total = ""
    ($cfg | ConvertTo-Json -Depth 12) | Set-Content -Path $configPath -Encoding UTF8
    Write-Host "auto_exit_configured=true mode=buy_and_sell price_multiplier=$AutoExitPriceMultiplier delay_seconds=$AutoExitDelaySeconds"
}

function Is-AutoExitConfigured {
    $configPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json"
    if (-not (Test-Path $configPath)) {
        return $false
    }
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        return [bool]$cfg.live_auto_exit_enabled
    } catch {
        return $false
    }
}

function Wait-ForAutoExitSellEvidence([DateTime]$RunStartUtc, [int]$TimeoutSeconds) {
    $autoExitActive = ($EnableAutoExit -or (Is-AutoExitConfigured))
    if (-not $autoExitActive) {
        return
    }
    $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(5, $TimeoutSeconds))
    while ([DateTime]::UtcNow -lt $deadline) {
        $auditFile = Get-ChildItem "data/exports" -Filter "live_pilot_service_auto*.jsonl" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
        if ($null -ne $auditFile -and $auditFile.LastWriteTimeUtc -ge $RunStartUtc) {
            $completed = @()
            foreach ($line in (Get-Content $auditFile.FullName)) {
                if (-not $line) { continue }
                try {
                    $row = $line | ConvertFrom-Json
                } catch {
                    continue
                }
                if ($row.event_type -eq "live_pilot_service_completed") {
                    $completed += $row
                }
            }
            if ($completed.Count -gt 0) {
                $latest = $completed[-1]
                $rollup = $latest.payload.rollup
                $buySigs = 0
                $sellSigs = 0
                try { $buySigs = [int]$rollup.submitted_signatures } catch {}
                try { $sellSigs = [int]$rollup.sell_submitted_signatures } catch {}
                if ($buySigs -le 0) {
                    return
                }
                if ($sellSigs -ge 1) {
                    Write-Host "auto_exit_sell_evidence_ok=true buy_submitted=$buySigs sell_submitted=$sellSigs"
                    return
                }
            }
        }
        Start-Sleep -Seconds 2
    }
    throw "auto_exit_safety_failed: buy_submitted_but_no_sell_submit_within_${TimeoutSeconds}s"
}

function Invoke-PythonWithWatchdog([string[]]$PyArgs, [int]$TimeoutSeconds, [string]$Label) {
    $safeArgs = @($PyArgs | Where-Object { $_ -ne $null -and (($_ | Out-String).Trim()) -ne "" })
    if ($safeArgs.Count -eq 0) {
        throw ("watchdog_invalid_args_" + $Label)
    }
    $proc = Start-Process -FilePath "python" -ArgumentList $safeArgs -PassThru -NoNewWindow
    $completed = $true
    try {
        Wait-Process -Id $proc.Id -Timeout $TimeoutSeconds -ErrorAction Stop
    } catch {
        $completed = $false
    }
    if (-not $completed) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
        throw ("watchdog_timeout_" + $Label + "_" + $TimeoutSeconds + "s")
    }
    return [int]$proc.ExitCode
}

function Get-LatestSignature {
    $receipt = Try-ReadJson "data/exports/v16_latest_live_receipt.json"
    if ($null -ne $receipt -and $receipt.signature) {
        return [string]$receipt.signature
    }
    return ""
}

function Write-RunSummary(
    [string]$Status,
    [string]$Reason,
    [int]$ExitCode,
    [object]$GateOk,
    [object]$SecurityOk,
    [object]$CloseoutOk,
    [string]$Signature,
    [string]$IncidentStage,
    [string]$IncidentType
) {
    $outPath = "data/exports/v16_autonomous_run_summary.json"
    $payload = @{
        ok = $true
        report_version = "v16_autonomous_run_summary_v1"
        generated_at_utc = [DateTime]::UtcNow.ToString("o")
        summary = @{
            status = $Status
            reason = $Reason
            exit_code = $ExitCode
            latest_signature = $Signature
            incident_stage = $IncidentStage
            incident_type = $IncidentType
        }
        gates = @{
            v16_go_no_go = $GateOk
            v2_security_preflight = $SecurityOk
            v2_closeout = $CloseoutOk
        }
    }
    ($payload | ConvertTo-Json -Depth 8) | Set-Content -Path $outPath -Encoding UTF8
    Write-Host "autonomous_run_summary_json=$outPath"
    return $payload
}

function Send-FailureAlert([hashtable]$SummaryPayload) {
    $url = $AlertWebhookUrl
    if (-not $url) {
        $url = $env:CRYPTO_SNIPER_ALERT_WEBHOOK_URL
    }
    if (-not $url) {
        return
    }
    $isDiscord = $url -like "https://discord.com/api/webhooks/*"
    if ($isDiscord) {
        $s = $SummaryPayload.summary
        $g = $SummaryPayload.gates
        $content = "crypto_sniper_failure status=$($s.status) reason=$($s.reason) exit_code=$($s.exit_code) security=$($g.v2_security_preflight) go_no_go=$($g.v16_go_no_go) closeout=$($g.v2_closeout)"
        $body = @{
            content = $content
            username = "crypto-sniper"
        } | ConvertTo-Json -Depth 4
    } else {
        $body = @{
            type = "crypto_sniper_failure"
            generated_at_utc = [DateTime]::UtcNow.ToString("o")
            summary = $SummaryPayload.summary
            gates = $SummaryPayload.gates
        } | ConvertTo-Json -Depth 8
    }
    try {
        Invoke-RestMethod -Method Post -Uri $url -ContentType "application/json" -Body $body -TimeoutSec 10 | Out-Null
        Write-Host "alert_webhook_sent=true"
    } catch {
        Write-Host "alert_webhook_sent=false"
    }
}

function Send-EventAlert([string]$EventType, [string]$Message, [hashtable]$Extra = @{}) {
    $url = $AlertWebhookUrl
    if (-not $url) {
        $url = $env:CRYPTO_SNIPER_ALERT_WEBHOOK_URL
    }
    if (-not $url) {
        return
    }
    $isDiscord = $url -like "https://discord.com/api/webhooks/*"
    if ($isDiscord) {
        $body = @{
            content = "crypto_sniper_$EventType $Message"
            username = "crypto-sniper"
        } | ConvertTo-Json -Depth 4
    } else {
        $body = @{
            type = "crypto_sniper_$EventType"
            generated_at_utc = [DateTime]::UtcNow.ToString("o")
            message = $Message
            extra = $Extra
        } | ConvertTo-Json -Depth 8
    }
    try {
        Invoke-RestMethod -Method Post -Uri $url -ContentType "application/json" -Body $body -TimeoutSec 10 | Out-Null
        Write-Host "event_alert_sent=true type=$EventType"
    } catch {
        Write-Host "event_alert_sent=false type=$EventType"
    }
}

function Emit-TradeAlertsIfPresent([DateTime]$RunStartUtc) {
    $receiptPath = "data/exports/v16_latest_live_receipt.json"
    if (-not (Test-Path $receiptPath)) {
        return
    }
    $info = Get-Item $receiptPath
    if ($info.LastWriteTimeUtc -lt $RunStartUtc) {
        return
    }
    $receipt = Try-ReadJson $receiptPath
    if ($null -eq $receipt) {
        return
    }
    $selected = Try-ReadJson "data/exports/v16_selected_promoted_candidate.json"
    $cfg = Try-ReadJson "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json"

    $sig = [string]$receipt.signature
    if (-not $sig) {
        return
    }
    $symbol = "UNKNOWN"
    $token = ""
    $entryPrice = ""
    $usdSize = ""
    if ($null -ne $selected) {
        if ($selected.symbol) { $symbol = [string]$selected.symbol }
        if ($selected.token_address) { $token = [string]$selected.token_address }
        if ($selected.features -and $selected.features.entry_price) { $entryPrice = [string]$selected.features.entry_price }
    }
    if ($null -ne $cfg -and $cfg.max_order_usd_cap) {
        $usdSize = [string]$cfg.max_order_usd_cap
    }

    $status = ""
    $txPresent = $false
    $solscan = ""
    $slippage = ""
    if ($receipt.rpc_status) {
        $status = [string]$receipt.rpc_status.confirmation_status
    }
    if ($receipt.solscan_url) {
        $solscan = [string]$receipt.solscan_url
    }
    if ($receipt.live_pilot_summary -and $receipt.live_pilot_summary.economics -and $receipt.live_pilot_summary.economics.realized_slippage_bps_vs_quote -ne $null) {
        $slippage = [string]$receipt.live_pilot_summary.economics.realized_slippage_bps_vs_quote
    }
    if ($receipt.tx_present -eq $true) {
        $txPresent = $true
    }
    $buyMsg = @(
        "BUY EXECUTED",
        "Token: $symbol",
        "Address: $token",
        "Entry Price: $entryPrice",
        "USD Size: $usdSize",
        "Status: $status",
        "Solscan: $solscan"
    ) -join "`n"
    Send-EventAlert -EventType "trade_executed" -Message $buyMsg
    if ($status -eq "finalized" -or $txPresent) {
        $settledMsg = @(
            "TRADE SETTLED",
            "Token: $symbol",
            "Address: $token",
            "Entry Price: $entryPrice",
            "Final Status: $status",
            "Tx Present: $txPresent",
            "Realized Slippage (bps): $slippage",
            "Solscan: $solscan"
        ) -join "`n"
        Send-EventAlert -EventType "trade_settled" -Message $settledMsg
    }
}

$cycleStatus = "failed"
$reason = ""
$exitCode = 20
$gateOk = $null
$securityOk = $null
$closeoutOk = $null
$signature = ""
$incidentStage = ""
$incidentType = ""

try {
    Set-SignerEnv
    Configure-AutoExitIfEnabled

    $releaseArgs = @(
        ".\examples\run_live_pilot_workflow_preset.py",
        "--preset", "v16_release_cycle",
        "--exports-dir", "data/exports"
    )
    if ($RelaxedTest) {
        $releaseArgs += @(
            "--min-liquidity-usd", "0",
            "--max-pair-age-seconds", "50000000",
            "--min-volume-5m-usd", "0",
            "--max-abs-price-change-5m-pct", "100",
            "--promote-min-score-total", "0",
            "--skip-v2-live-gate-enforcement"
        )
    }
    $releaseCode = Invoke-PythonWithWatchdog -PyArgs $releaseArgs -TimeoutSeconds $ReleaseCycleTimeoutSeconds -Label "v16_release_cycle"
    if ($releaseCode -ne 0) {
        $incident = Try-ReadJson "data/exports/v16_release_cycle_incident.json"
        if ($null -ne $incident -and $incident.summary) {
            $incidentStage = [string]$incident.summary.stage
            $incidentType = [string]$incident.summary.error_type
            $reason = [string]$incident.summary.error_message
            if ($reason -eq "v16_no_promoted_candidates_after_scoring") {
                $cycleStatus = "no_trade"
                $exitCode = 0
            } else {
                $cycleStatus = "failed"
                if (-not $reason) {
                    $reason = "release_cycle_failed"
                }
                $exitCode = 20
            }
        } else {
            $cycleStatus = "failed"
            $reason = "release_cycle_failed_no_incident_packet"
            $exitCode = 20
        }
    } else {
        $gate = Try-ReadJson "data/exports/v16_go_no_go_gate.json"
        $security = Try-ReadJson "data/exports/v2_security_preflight.json"
        $closeout = Try-ReadJson "data/exports/v2_closeout_packet.json"

        $gateOk = ($null -ne $gate -and [bool]$gate.go)
        $securityOk = ($null -ne $security -and $null -ne $security.summary -and [bool]$security.summary.preflight_ok)
        $closeoutOk = ($null -ne $closeout -and $null -ne $closeout.summary -and [bool]$closeout.summary.enable_v2_default_live_gate)

        if (-not $gateOk -or -not $securityOk -or -not $closeoutOk) {
            $cycleStatus = "failed"
            $reason = "production_gate_failed gate=$gateOk security=$securityOk closeout=$closeoutOk"
            $exitCode = 30
        } else {
            Wait-ForAutoExitSellEvidence -RunStartUtc $runStartUtc -TimeoutSeconds $AutoExitSellWaitTimeoutSeconds
            $cycleStatus = "success"
            $reason = "production_cycle_ok"
            $exitCode = 0
        }
    }
} catch {
    $cycleStatus = "failed"
    $reason = "autonomous_runner_exception: $($_.Exception.Message)"
    $exitCode = 20
}

$signature = Get-LatestSignature
$summaryPayload = Write-RunSummary `
    -Status $cycleStatus `
    -Reason $reason `
    -ExitCode $exitCode `
    -GateOk $gateOk `
    -SecurityOk $securityOk `
    -CloseoutOk $closeoutOk `
    -Signature $signature `
    -IncidentStage $incidentStage `
    -IncidentType $incidentType

if ($exitCode -ne 0) {
    Send-FailureAlert -SummaryPayload $summaryPayload
}

if ($cycleStatus -eq "success") {
    Emit-TradeAlertsIfPresent -RunStartUtc $runStartUtc
    Write-Host "production_cycle_ok gate=$gateOk security=$securityOk closeout=$closeoutOk"
} elseif ($cycleStatus -eq "no_trade") {
    Write-Host "no_trade_cycle_ok reason=$reason"
}

exit $exitCode
