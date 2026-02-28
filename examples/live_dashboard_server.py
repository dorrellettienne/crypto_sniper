#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORTS_DIR = REPO_ROOT / "data" / "exports"
FRONTEND_FILE = REPO_ROOT / "frontend" / "live_ops_dashboard.html"
ACCESS_TOKEN = ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _latest_auto_window_log() -> Path | None:
    hits = sorted(
        glob(str(EXPORTS_DIR / "live_pilot_service_auto_window_*.jsonl")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    return Path(hits[0]) if hits else None


def _tail_jsonl_events(path: Path | None, n: int = 40) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    except Exception:
        return []
    return rows[-max(1, int(n)) :]


def _latest_completed_rollup(path: Path | None) -> dict[str, Any]:
    rollup: dict[str, Any] = {}
    for row in _tail_jsonl_events(path, n=500):
        if str(row.get("event_type") or "") != "live_pilot_service_completed":
            continue
        payload = row.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("rollup"), dict):
            rollup = dict(payload.get("rollup") or {})
    return rollup


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_status_payload(max_events: int = 20) -> dict[str, Any]:
    run_summary = _read_json(EXPORTS_DIR / "v16_autonomous_run_summary.json")
    readiness = _read_json(EXPORTS_DIR / "final_stage_readiness.json")
    gate = _read_json(EXPORTS_DIR / "v16_go_no_go_gate.json")
    security = _read_json(EXPORTS_DIR / "v2_security_preflight.json")
    closeout = _read_json(EXPORTS_DIR / "v2_closeout_packet.json")
    latest_log = _latest_auto_window_log()
    rollup = _latest_completed_rollup(latest_log)
    auto = dict(rollup.get("auto_window") or {})

    raw_events = _tail_jsonl_events(latest_log, n=max(5, int(max_events) * 2))
    keep_types = {
        "live_submit_dispatch",
        "live_chain_reconciliation",
        "live_pilot_service_cycle_completed",
        "live_pilot_service_completed",
        "live_pilot_service_summary",
    }
    events: list[dict[str, Any]] = []
    for row in raw_events:
        et = str(row.get("event_type") or "")
        if et not in keep_types:
            continue
        payload = row.get("payload")
        brief: dict[str, Any] = {
            "event_type": et,
            "timestamp_utc": row.get("ts"),
        }
        if isinstance(payload, dict):
            for key in (
                "reason",
                "action",
                "submitted_signature",
                "submit_dispatch_reason",
                "chain_outcome_class",
                "cycle",
                "mode",
            ):
                if key in payload:
                    brief[key] = payload.get(key)
            if et == "live_pilot_service_completed":
                rr = payload.get("rollup") if isinstance(payload.get("rollup"), dict) else {}
                brief["submitted_signatures"] = rr.get("submitted_signatures")
                brief["sell_submitted_signatures"] = rr.get("sell_submitted_signatures")
        events.append(brief)
    events = events[-max(1, int(max_events)) :]

    return {
        "ok": True,
        "report_version": "live_dashboard_status_v1",
        "generated_at_utc": _iso_now(),
        "artifacts": {
            "latest_auto_window_log": str(latest_log) if latest_log else "",
            "run_summary_present": bool(run_summary),
            "readiness_present": bool(readiness),
        },
        "status": {
            "autonomous": dict(run_summary.get("summary") or {}),
            "gates": dict(run_summary.get("gates") or {}),
            "readiness": dict(readiness.get("summary") or {}),
            "go_no_go": bool(gate.get("go")) if gate else None,
            "security_preflight_ok": bool((security.get("summary") or {}).get("preflight_ok")) if security else None,
            "closeout_ok": bool((closeout.get("summary") or {}).get("enable_v2_default_live_gate")) if closeout else None,
        },
        "rollup": {
            "submitted_signatures": int(rollup.get("submitted_signatures", 0) or 0),
            "sell_submitted_signatures": int(rollup.get("sell_submitted_signatures", 0) or 0),
            "runs": int(rollup.get("runs", 0) or 0),
            "sell_runs": int(rollup.get("sell_runs", 0) or 0),
            "submit_dispatch_by_reason": dict(rollup.get("submit_dispatch_by_reason") or {}),
            "sell_submit_dispatch_by_reason": dict(rollup.get("sell_submit_dispatch_by_reason") or {}),
            "auto_window": {
                "cycles_completed": int(auto.get("cycles_completed", 0) or 0),
                "trades_submitted": int(auto.get("trades_submitted", 0) or 0),
                "sells_submitted": int(auto.get("sells_submitted", 0) or 0),
                "stop_reason": str(auto.get("stop_reason") or ""),
            },
        },
        "events": events,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CryptoSniperLiveDashboard/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, text: str, status: int = 200) -> None:
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        path = u.path or "/"
        qs = parse_qs(u.query or "")
        if ACCESS_TOKEN:
            qtok = str((qs.get("token") or [""])[0])
            htok = str(self.headers.get("X-Access-Token") or "")
            if qtok != ACCESS_TOKEN and htok != ACCESS_TOKEN:
                self._send_json({"ok": False, "error": "unauthorized"}, status=401)
                return
        if path in ("/", "/index.html"):
            if FRONTEND_FILE.exists():
                self._send_html(FRONTEND_FILE.read_text(encoding="utf-8"))
            else:
                self._send_html("<h1>Dashboard file missing</h1>", status=500)
            return
        if path == "/api/status":
            n = 20
            try:
                n = max(5, min(100, int((qs.get("events") or ["20"])[0])))
            except Exception:
                n = 20
            self._send_json(build_status_payload(max_events=n))
            return
        if path == "/healthz":
            self._send_json({"ok": True, "generated_at_utc": _iso_now()})
            return
        self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)


def main() -> int:
    global ACCESS_TOKEN
    p = argparse.ArgumentParser(description="Serve live crypto sniper dashboard over HTTP.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--access-token", default="")
    args = p.parse_args()
    ACCESS_TOKEN = str(args.access_token or "").strip()

    httpd = ThreadingHTTPServer((args.host, int(args.port)), Handler)
    base = f"http://{args.host}:{args.port}"
    print(
        json.dumps(
            {
                "ok": True,
                "listen": base,
                "healthz": f"http://127.0.0.1:{args.port}/healthz",
                "token_required": bool(ACCESS_TOKEN),
                "open_url_hint": (f"{base}/?token={ACCESS_TOKEN}" if ACCESS_TOKEN else f"{base}/"),
            }
        )
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
