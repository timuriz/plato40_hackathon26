# PLATO-40 Command Center UI

A standalone React + Vite dashboard. It does not execute any physical action; it replays what the
building knew, what the pipeline detected, and what it decided, using the causal `observe.asof(t)` view
plus the pipeline's `decisions.jsonl` and `incidents.jsonl` outputs.

## How it connects to the backend

```
run.py ──► out/decisions.jsonl          ┐
       └─► out/incidents.jsonl          │   (episode_id links an incident to its decisions)
                                         ▼
serve_ui.py --data data --decisions ... --incidents ...
   ├─ serves the built React app from ui/dist
   └─ exposes the replay API under /api/*
                                         ▲
ui (Vite) ── fetch('/api/...') ──────────┘   (dev server proxies /api to port 8090)
```

## Run it (production build)

From the repository root:

```bash
# 1. Generate the decisions + incidents the UI reads (rule-based, offline; add an API key for the LLM)
python run.py --data data --no-llm --out out/decisions.jsonl

# 2. Build the React app once (creates ui/dist)
npm run install:ui
npm run build

# 3. Serve the built UI and the replay API together on one port
python serve_ui.py --data data --decisions out/decisions.jsonl --incidents out/incidents.jsonl
```

Then open `http://127.0.0.1:8090`.

`run.py` writes `incidents.jsonl` next to `--out` automatically (override with `--incidents PATH`).
Drop `--no-llm` to use the Adaption LLM (needs `ADAPTION_API_KEY` in `.env`); add `--cache-dir .cache/llm`
for fast, identical reruns.

## Develop the UI with hot reload

Two terminals:

```bash
# Terminal 1 – replay API + dataset
python serve_ui.py --data data --decisions out/decisions.jsonl --incidents out/incidents.jsonl

# Terminal 2 – Vite dev server with hot reload (proxies /api to port 8090)
npm run dev
```

The Vite dev UI runs on `http://127.0.0.1:5173`.

## Data contract

- `GET /api/health`: pipeline profile, replay bounds, step count.
- `GET /api/dashboard?at=<ISO-8601 UTC>`: full state at the last real step ≤ `at` (server uses `obs.asof(t)`).
- `GET /api/room-history?at=<ISO-8601 UTC>&room_id=R101`: causal hourly temperature / setpoint / humidity / CO₂.
- `GET /api/replay/incident?at=...&direction=next|previous`: jump to the next/previous detected incident.
- `GET /api/replay/resolve-time?local=<YYYY-MM-DDTHH:MM>`: resolve a Europe/Vienna wall-clock time to UTC.

`--decisions` follows `DECISION_CONTRACT.md` (`event_id`, `timestamp_utc`, `affected_assets`, `issue_type`,
`action`, `priority`, `reason`, `requires_human_approval`, plus optional `autonomy`, `episode_id`, `evidence`,
`expected_outcome`, `verification_plan`, `confidence`). `--incidents` is the UNDERSTAND output
(`episode_id`, `timestamp_utc`, `issue_type`, `severity`, `confidence`, `reason`, `affected_assets`,
`evidence`, `features`). Both logs are shown only up to the selected replay time; an incident and a decision
are linked only when they share `episode_id`. A missing log path renders as "not connected".

Values unknown at the selected instant stay unknown (`—`). The UI invents no incidents, decisions, success
rates or yearly insights, and there is no approval or actuator endpoint connected. The 3D view derives
schematic room geometry from `building.json`; it is not a surveyed floor plan, and falls back to an
interactive 2D plan when WebGL is unavailable.
