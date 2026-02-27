param(
    [string]$PytestFilter = "entry_rule_config_from_feedback or score_band_gate_from_outcomes or exit_policy_fingerprint_from_outcomes or token_memory_weighted_outcome_summary"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

$results = [ordered]@{
    ok = $false
    checks = @()
}

function Add-Check([string]$Name, [bool]$Ok, [string]$Detail) {
    $results.checks += [ordered]@{
        name = $Name
        ok = $Ok
        detail = $Detail
    }
}

python -m py_compile .\examples\export_v14_strategy_cycle_signoff.py
if ($LASTEXITCODE -ne 0) {
    Add-Check "py_compile_signoff_export" $false "py_compile_failed"
    $results.ok = $false
    $results | ConvertTo-Json -Depth 8
    exit 1
}
Add-Check "py_compile_signoff_export" $true "ok"

python -m py_compile .\examples\export_v14_ops_bundles_index.py
if ($LASTEXITCODE -ne 0) {
    Add-Check "py_compile_ops_index_export" $false "py_compile_failed"
    $results.ok = $false
    $results | ConvertTo-Json -Depth 8
    exit 1
}
Add-Check "py_compile_ops_index_export" $true "ok"

pytest -q -p no:cacheprovider tests/test_live_pilot_service.py -k $PytestFilter
if ($LASTEXITCODE -ne 0) {
    Add-Check "pytest_v14_smoke" $false ("failed_filter=" + $PytestFilter)
    $results.ok = $false
    $results | ConvertTo-Json -Depth 8
    exit 1
}
Add-Check "pytest_v14_smoke" $true ("passed_filter=" + $PytestFilter)

$results.ok = $true
$results | ConvertTo-Json -Depth 8
exit 0

