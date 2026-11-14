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
rates or unsupported yearly insights, and there is no approval or actuator endpoint connected. The 3D view derives
schematic room geometry from `building.json`; it is not a surveyed floor plan, and falls back to an
interactive 2D plan when WebGL is unavailable.

## Midnight Glass and replay limitations

The visual theme is implemented in `src/midnight-glass.css`, layered over the existing component styles.
It uses native system fonts, bounded blur on the navigation surfaces and a non-blurred inspector alongside the
3D scene so that camera interaction does not require a backdrop filter on every frame. Reduced-motion
preferences are respected. Critical state colors remain distinct from the blue navigation accents.

The Building Digital Twin is schematic: the facade, stairs, roof, entrance and EXIT markings illustrate
possible geometry and are **not** a verified building or emergency plan. Staff/robot markers are the last
reported room or area from a cleaning event or granted access record, with observation time and source;
they are not live coordinates. A shift or dispatch alone does not establish presence or task availability.

The Yearly Insights cards calculate two descriptive recommendations from equipment-sensor missingness
in the interval available up to the replay cursor. The quickstart is 21 days, not a full year; neither a
missing reading nor the recommendation proves an equipment fault or predicts savings. Timestamped external
recommendations can also be attached with `--insights path/to/insights.jsonl`. Entries without a timestamp
are excluded from replay to prevent future information leaking into earlier timestamps.

The optional `--actions path/to/actions.jsonl` connects a **read-only** timestamped audit log. Each row
must have `timestamp_utc` and `decision_event_id`; `approval_status`, `consent_status`, `execution_status`,
`verification_status` and `verification_evidence` are optional. A simulated execution is only labelled
when its record explicitly says so; verification additionally requires recorded evidence. Neither an
approval interface nor an executor is provided. The safety guard shapes proposals, but resource reservations,
complete release eligibility, human approval and guest consent still require an execution-time backend.

See [HACKATHON_STATUS.md](HACKATHON_STATUS.md) for the feature audit and remaining integration gaps.

## Activity & Decisions

The unified timeline replaces separate scenario views. `GET /api/activity?at=<UTC>` projects historical
building events, guest requests, time-stamped data-quality findings, incidents, decisions and optional audit
records into a common read-only model. Each entry keeps the original source record in `metadata`; it does
not alter the official decisions contract. Explicit `available_at` / `available_at_utc` / `published_utc`
are respected in addition to event time. Undated quality findings and unavailable future records are excluded.
Sensor values stay in the analytics and room inspector; only alarms and data-quality findings appear as
activity, and neither is automatically a confirmed fault.

Optional query parameters: `type` (`all`, `events`, `requests`, `anomalies`, `incidents`, `decisions`,
`actions`, `approvals`, `verification`); `asset`, `severity`, `issue_type`, `status`; `from_date` and
`to_date` as Europe/Vienna calendar dates; `limit` (1–100); and `cursor` from the prior response's
`next_cursor`. Paging uses timestamp plus stable ID so simultaneous records are not skipped. Timeline entries
are ordered by event time; an explicitly delayed availability time is shown in the detail panel.

The timeline links incidents and decisions only through an explicit episode/incident ID. Action audit records
are connected only through `decision_event_id`. Selecting an entry selects its room in the 3D twin where
possible; a non-room asset is shown as a building-level selection and can be inspected in analytics.
Room readings and the context panel reflect the **current replay cursor**, not necessarily the original
detection time. Available detector evidence remains labelled separately. No executor, approval backend,
actual guest delivery or verified result is implied by a proposal. External actions and approval states are
visible only when a timestamped `--actions` audit log is supplied.

The timeline is capped to 100 records per API response and loads older results on demand. The currently
selected timestamp remains the common upper bound even when filtering by dates or loading additional pages.

## Architectural digital twin

The floor generator uses configured room IDs, floors, north/south orientation and area. A 6 m schematic
room depth and a 2.5 m per scene-unit scale are visualization assumptions; widths vary with the supplied
areas, so the relative footprints remain proportional. A shared column grid keeps the modules aligned
across floors; the corridor, door openings and cores are generated as architectural visualization geometry.
A missing room area uses a neutral placeholder footprint and is not reported as a measurement.

Building mode has a schematic white facade and an enclosed ground-floor arrival wing. Its main entrance
opens into a lobby connected to the eastern stair core and the room corridor; the western core is marked
as illustrative emergency access. The uppermost floor shows a terminating landing, without an upward
stair to the roof. This layout preserves all ten configured rooms on every floor and is an architectural
assumption, not a surveyed circulation plan. Floor mode exposes the lobby and corridor as a cutaway;
Exploded mode separates aligned floor plates. Zoom, orbit, pan, room selection and replay
remain available. The equipment tab and model markers derive their IDs and types from `building.json`.
A specified equipment floor or lobby association is respected; all other placements (including the rooftop
PV and technical grouping) are schematic, not evidence of physical location. Equipment measurements and
quality are read from the replay, with the latest source timestamp for hourly logistics. Incident markers
represent recorded detections, not confirmed faults. Exit signs and the complete spatial arrangement must
not be used as an evacuation plan.

Layout regression checks: `node --test ui/tests/buildingLayout.test.js`.
