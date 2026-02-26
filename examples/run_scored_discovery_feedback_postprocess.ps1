param(
    [string]$ExportsDir = "data/exports",
    [string]$ScoredReportJsonPath = "data/exports/scored_discovery_report.json",
    [string]$ReceiptJsonPath = "data/exports/latest_live_receipt.json",
    [string]$StrategyTraceJsonPath = "data/exports/strategy_decision_trace.json",
    [string]$OutcomeLogJsonlPath = "data/exports/scored_candidate_outcomes.jsonl",
    [string]$CalibrationSummaryJsonPath = "data/exports/scored_candidate_calibration_summary.json",
    [string]$CalibrationSummaryMdPath = "data/exports/scored_candidate_calibration_summary.md",
    [string]$ContextRunLabel = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

if (-not (Test-Path $ScoredReportJsonPath)) {
    throw "scored_report_not_found: $ScoredReportJsonPath"
}

if (-not (Test-Path $ReceiptJsonPath)) {
    Write-Host "feedback-postprocess: receipt missing, exporting latest receipt"
    python .\examples\export_latest_live_submit_receipt.py --exports-dir $ExportsDir --output-json $ReceiptJsonPath
    if ($LASTEXITCODE -ne 0) { throw "receipt_export_failed" }
}

$py = @'
import argparse
import hashlib
import json
from pathlib import Path
from src.live.live_pilot_service import (
    append_live_pilot_scored_candidate_outcome_log,
    build_live_pilot_scored_candidate_calibration_summary,
    write_live_pilot_scored_candidate_calibration_summary,
)

def _json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))

def _exit_policy_context(strategy_trace_json_path: str) -> dict:
    p = Path(strategy_trace_json_path)
    if not p.exists():
        return {}
    try:
        trace = _json(p)
    except Exception:
        return {}
    schema = (((trace.get("strategy_decision_trace") or {}).get("exit_policy") or {}).get("schema") or {})
    if not isinstance(schema, dict) or not schema:
        return {}
    raw = json.dumps(schema, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    fp = hashlib.sha256(raw).hexdigest()
    return {
        "exit_policy_fingerprint": fp,
        "exit_policy_label": str(schema.get("exit_policy_version") or "v1.3_exit_policy_schema_v1"),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored-report-json-path", required=True)
    ap.add_argument("--receipt-json-path", required=True)
    ap.add_argument("--strategy-trace-json-path", default="")
    ap.add_argument("--outcome-log-jsonl-path", required=True)
    ap.add_argument("--calibration-summary-json-path", required=True)
    ap.add_argument("--calibration-summary-md-path", required=True)
    ap.add_argument("--context-run-label", default="")
    args = ap.parse_args()

    scored = _json(args.scored_report_json_path)
    receipt = _json(args.receipt_json_path)
    extra_context = {"run_label": args.context_run_label}
    extra_context.update(_exit_policy_context(args.strategy_trace_json_path or ""))
    append_res = append_live_pilot_scored_candidate_outcome_log(
        args.outcome_log_jsonl_path,
        scored_discovery_report=scored,
        receipt=receipt,
        extra_context=extra_context,
    )
    rows = []
    p = Path(args.outcome_log_jsonl_path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    summary = build_live_pilot_scored_candidate_calibration_summary(rows)
    write_live_pilot_scored_candidate_calibration_summary(summary, args.calibration_summary_json_path)
    write_live_pilot_scored_candidate_calibration_summary(summary, args.calibration_summary_md_path)
    print(json.dumps({
        "ok": True,
        "append": append_res,
        "calibration_rows_total": summary.get("rows_total", 0),
        "promoted_count": ((summary.get("metrics") or {}).get("promoted_count", 0)),
        "promoted_finalized_rate": ((summary.get("metrics") or {}).get("promoted_finalized_rate")),
        "output_json": args.calibration_summary_json_path,
        "output_md": args.calibration_summary_md_path,
    }, separators=(",", ":"), default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'@

$tmpPy = Join-Path $ExportsDir "_scored_discovery_feedback_postprocess_tmp.py"
[System.IO.Directory]::CreateDirectory((Split-Path -Parent $tmpPy)) | Out-Null
[System.IO.File]::WriteAllText($tmpPy, $py, (New-Object System.Text.UTF8Encoding($false)))

try {
    Write-Host "feedback-postprocess: append scored outcome log + export calibration summary"
    python $tmpPy `
      --scored-report-json-path $ScoredReportJsonPath `
      --receipt-json-path $ReceiptJsonPath `
      --strategy-trace-json-path $StrategyTraceJsonPath `
      --outcome-log-jsonl-path $OutcomeLogJsonlPath `
      --calibration-summary-json-path $CalibrationSummaryJsonPath `
      --calibration-summary-md-path $CalibrationSummaryMdPath `
      --context-run-label $ContextRunLabel
    if ($LASTEXITCODE -ne 0) { throw "scored_feedback_postprocess_failed" }
}
finally {
    if (Test-Path $tmpPy) { Remove-Item $tmpPy -ErrorAction SilentlyContinue }
}

Write-Host "feedback-postprocess: done"
