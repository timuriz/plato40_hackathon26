#!/usr/bin/env python3
"""Serve the standalone command-center UI and the existing replay pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from plato.observe import observe
from plato.ui_payload import build_dashboard_payload, build_room_history
from plato.ui_intelligence import next_incident_time


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, observation, decisions: str | None, incidents: str | None,
                 actions=None, insights=None, **kwargs):
        self.observation = observation
        self.decisions = decisions
        self.incidents = incidents
        self.actions = actions
        self.insights = insights
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/api/replay/incident", "/api/replay/resolve-time"}:
            values = parse_qs(parsed.query)
            try:
                if parsed.path.endswith("resolve-time"):
                    local = pd.Timestamp(values.get("local", [""])[0])
                    if local.tzinfo is not None:
                        raise ValueError("Provide local building time without an offset")
                    stamp = local.tz_localize("Europe/Vienna", ambiguous="raise", nonexistent="raise").tz_convert("UTC")
                    if pd.isna(stamp) or stamp < self.observation.grid[0] or stamp > self.observation.grid[-1]:
                        raise ValueError("Time is outside the dataset range")
                    return self._json({"timestamp_utc": stamp.isoformat()})
                stamp = pd.to_datetime(values.get("at", [None])[0], utc=True)
                direction = values.get("direction", ["next"])[0]
                if stamp is None or pd.isna(stamp) or direction not in {"next", "previous"}:
                    raise ValueError("Provide a valid at timestamp and next/previous direction")
                return self._json(next_incident_time(self.observation, self.incidents, stamp, direction))
            except Exception as exc:
                # pandas also raises timezone-specific ambiguity/nonexistent-time errors.
                return self._json({"error": str(exc)}, status=400)
        if parsed.path == "/api/health":
            payload = {"status": "ok", "pipeline": "observe", "profile": self.observation.manifest.get("profile"),
                       "start_utc": self.observation.grid[0].isoformat() if len(self.observation.grid) else None,
                       "end_utc": self.observation.grid[-1].isoformat() if len(self.observation.grid) else None,
                       "step_count": len(self.observation.grid), "step_minutes": 15}
            return self._json(payload)
        if parsed.path == "/api/dashboard":
            values = parse_qs(parsed.query)
            at = values.get("at", [None])[0] or self.observation.grid[0].isoformat()
            selected_room_id = values.get("room_id", [None])[0]
            try:
                payload = build_dashboard_payload(self.observation, at, self.decisions, self.incidents, selected_room_id,
                                                  actions_path=self.actions, insights_path=self.insights)
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._json(payload)
        if parsed.path == "/api/room-history":
            values = parse_qs(parsed.query)
            at = values.get("at", [None])[0] or self.observation.grid[0].isoformat()
            room_id = values.get("room_id", [""])[0]
            try:
                return self._json(build_room_history(self.observation, at, room_id))
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, status=400)
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[ui] " + fmt % args + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the PLATO-40 command center with causal replay data")
    parser.add_argument("--data", required=True, help="dataset directory (quickstart, dev or holdout)")
    parser.add_argument("--decisions", help="optional decisions.jsonl produced by the decision pipeline")
    parser.add_argument("--incidents", help="optional incidents.jsonl produced by the detection pipeline")
    parser.add_argument("--actions", help="optional timestamped action/approval/verification audit JSONL (read-only)")
    parser.add_argument("--insights", help="optional timestamped insights.jsonl; undated records are excluded from replay")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)
    try:
        obs = observe(args.data)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    ui_dir = Path(__file__).parent / "ui" / "dist"
    if not ui_dir.is_dir():
        parser.error("React UI is not built yet; run `cd ui && npm install && npm run build` first")
    handler = lambda *handler_args, **kwargs: Handler(*handler_args, directory=str(ui_dir), observation=obs,
                                                       decisions=args.decisions, incidents=args.incidents,
                                                       actions=args.actions, insights=args.insights, **kwargs)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"PLATO-40 UI: http://{args.host}:{args.port}  profile={obs.manifest.get('profile')}  steps={len(obs.grid)}")
    print("Replay API: /api/dashboard?at=<UTC ISO timestamp>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI server")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
