param(
    [string]$ConfigPath = "data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json",
    [double]$ExpectedUsdSize = 0.25
)

$ErrorActionPreference = "Stop"

$result = [ordered]@{
    ok = $false
    checks = @()
    signer_pubkey = ""
    config_path = $ConfigPath
}

function Add-Check([string]$Name, [bool]$Ok, $Actual, $Expected, [string]$Severity = "error") {
    $row = [ordered]@{
        name = $Name
        ok = $Ok
        actual = $Actual
        expected = $Expected
        severity = $Severity
    }
    $result.checks += $row
}

if (-not (Test-Path $ConfigPath)) {
    Add-Check "config_exists" $false $false $true
    $result.ok = $false
    $result | ConvertTo-Json -Depth 8
    exit 1
}
Add-Check "config_exists" $true $true $true

$cfg = Get-Content -Raw $ConfigPath | ConvertFrom-Json

$hasKey = [bool]$env:SOLANA_PILOT_PRIVATE_KEY_B58
Add-Check "env_SOLANA_PILOT_PRIVATE_KEY_B58_present" $hasKey $hasKey $true

$pubJson = $null
if ($hasKey) {
    $pubOut = python .\examples\print_solana_signer_pubkey_from_env.py
    if ($pubOut) {
        try { $pubJson = $pubOut | ConvertFrom-Json } catch { $pubJson = $null }
    }
}

if ($pubJson -and $pubJson.ok) {
    $result.signer_pubkey = [string]$pubJson.pubkey
    Add-Check "signer_key_parse_ok" $true $true $true
    Add-Check "signer_key_decoded_len_valid" (($pubJson.decoded_key_len -eq 32) -or ($pubJson.decoded_key_len -eq 64)) $pubJson.decoded_key_len "32_or_64"
    Add-Check "wallet_public_key_matches_signer" ([string]$cfg.wallet_public_key -eq [string]$pubJson.pubkey) ([string]$cfg.wallet_public_key) ([string]$pubJson.pubkey)
} elseif ($hasKey) {
    Add-Check "signer_key_parse_ok" $false "invalid" "valid_base58_key"
}

Add-Check "live_send_network_enabled" ([bool]$cfg.live_send_network_enabled) ([bool]$cfg.live_send_network_enabled) $true
Add-Check "dex_quote_only_mode_false" (-not [bool]$cfg.dex_quote_only_mode) ([bool]$cfg.dex_quote_only_mode) $false
Add-Check "max_order_usd_cap" ([double]$cfg.max_order_usd_cap -le [double]$ExpectedUsdSize) ([double]$cfg.max_order_usd_cap) ("<= " + $ExpectedUsdSize)
Add-Check "pilot_hard_max_order_usd_cap" ([double]$cfg.pilot_hard_max_order_usd_cap -le [double]$ExpectedUsdSize) ([double]$cfg.pilot_hard_max_order_usd_cap) ("<= " + $ExpectedUsdSize)
Add-Check "live_send_max_notional_usd_total" ([double]$cfg.live_send_max_notional_usd_total -le [double]$ExpectedUsdSize) ([double]$cfg.live_send_max_notional_usd_total) ("<= " + $ExpectedUsdSize)
Add-Check "live_send_max_orders_per_session" ([int]$cfg.live_send_max_orders_per_session -eq 1) ([int]$cfg.live_send_max_orders_per_session) 1

$allowlist = @($cfg.allowlist_tokens)
$hasUsdc = $allowlist -contains "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
Add-Check "allowlist_includes_usdc" $hasUsdc $hasUsdc $true

$errorChecks = @($result.checks | Where-Object { (-not $_.ok) -and $_.severity -eq "error" })
$result.ok = ($errorChecks.Count -eq 0)
$result | ConvertTo-Json -Depth 8
if (-not $result.ok) { exit 1 }
