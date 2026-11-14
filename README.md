# PLATO-40 — The Autonomous Building Challenge

> An entry for **PLATO × CLARA Future — The Autonomous Building Challenge** (Proximata Hackathon '26, Rhomberg track).

PLATO-40 is a fictional 40-room, four-floor self-check-in hotel. This project replays a year of its
synthetic operating data and runs it through an autonomous building loop: it **observes** the raw signals,
**understands** what is going on (with evidence and uncertainty), **decides and acts** safely within the
autonomy rules, **records** every decision to an auditable log, and **learns** lessons to feed back into
**CLARA** (the operating software) and **PLATO** (the next building). Detection is deterministic and causal;
an optional LLM explains candidate incidents, and a plain-code safety guard has the final say on every action
so the system is reproducible with or without an API key. All findings concern the fictional PLATO-40 only.

---

## Quickstart

Requires Python 3.10+.

```bash
# 1. Install
pip install -r requirements.txt

# 2. One-command run: reads the dataset, writes decisions.jsonl (+ incidents.jsonl)
python3 run.py --data data
```

`run.py` is the single documented command the challenge asks for: it takes a dataset directory and writes a
`decisions.jsonl` that follows [`DECISION_CONTRACT.md`](DECISION_CONTRACT.md). It also writes an
`incidents.jsonl` (the UNDERSTAND output the UI reads) next to the decision log.

### Common invocations

```bash
# Deterministic, fully offline (rule-based UNDERSTAND; no network, no API key)
python3 run.py --data data --no-llm

# Choose output paths and a cache for identical, faster reruns
python3 run.py --data data --out out/decisions.jsonl --incidents out/incidents.jsonl --cache-dir .cache/llm

# Only the OBSERVE stage, with a data-quality report
python3 run.py --data data --observe-only --report-json out/observe_report.json

# Attach a JSON data-quality report to a full run as well
python3 run.py --data data --report-json out/observe_report.json
```

### `run.py` flags

| Flag | Default | Purpose |
|---|---|---|
| `--data` | *(required)* | Dataset directory (quickstart, dev or holdout) |
| `--out` | `decisions.jsonl` | Decision-log output path |
| `--incidents` | `incidents.jsonl` next to `--out` | UNDERSTAND/incident log for the UI |
| `--no-llm` | off | Skip the LLM; rule-based UNDERSTAND only (deterministic, offline) |
| `--model` | client default | Adaption model id |
| `--cache-dir` | — | Cache LLM answers (identical reruns, faster) |
| `--llm-workers` | `12` | Parallel LLM calls (the Adaption key throttles past ~12) |
| `--observe-only` | off | Run only OBSERVE and print its report |
| `--report-json` | — | Also write the data-quality report as JSON |

**LLM vs no-LLM.** Without an `ADAPTION_API_KEY` (or with `--no-llm`) the run is fully deterministic and
offline: the rule-based classifier stands in for the LLM, so the one-command run always produces a valid
`decisions.jsonl`. With a key set in `.env`, the LLM reasons over the deterministic evidence packets; the
safety guard still enforces every rule afterwards.

**Holdout run.** The holdout dataset is processed by the *unchanged* pipeline, just by pointing `--data` at it
and writing to a separate file:

```bash
python3 run.py --data /path/to/holdout --out decisions_holdout.jsonl --no-llm
```

### UI (command center)

A standalone React + Vite dashboard replays what the building knew, what the pipeline detected and what it
decided. It executes no physical action and invents nothing — unknown values stay unknown, and an incident is
linked to a decision only when they share an `episode_id`.

```bash
# 1. Produce the logs the UI reads
python3 run.py --data data --no-llm --out out/decisions.jsonl

# 2. Build the React app once (creates ui/dist)
npm run install:ui
npm run build

# 3. Serve the built UI and the replay API together on one port
python3 serve_ui.py --data data --decisions out/decisions.jsonl --incidents out/incidents.jsonl
```

Then open `http://127.0.0.1:8090`. `serve_ui.py` serves the built app from `ui/dist` and exposes a causal
replay API under `/api/*` (it errors out with a hint if the UI has not been built yet). Flags:
`--data` (required), `--decisions`, `--incidents`, `--actions`, `--insights`, `--host` (default `127.0.0.1`),
`--port` (default `8090`). See [`ui/README.md`](ui/README.md) for the dev-server/hot-reload workflow and the
full API contract.

---

## Architecture

A five-stage pipeline. Everything in OBSERVE, the detectors, episode grouping and the safety guard is plain,
deterministic code; the LLM is confined to explaining candidate incidents and can always be replaced by the
rule-based fallback.

```mermaid
flowchart LR
    data[(Dataset dir)] --> OBS

    subgraph OBS[OBSERVE]
      direction TB
      load[Load + clean] --> ql[Quality labels]
      ql --> asof["Causal as-of view (obs.asof t)"]
    end

    subgraph UND[UNDERSTAND]
      direction TB
      det["Causal detectors<br/>S01-S05 + logistics + energy"] --> ep[Episode grouping]
      ep --> cls{LLM?}
      cls -- key set --> llm[LLM report_incident]
      cls -- no key / --no-llm --> rule[Rule-based fallback]
    end

    subgraph DEC[DECIDE & ACT]
      direction TB
      pb[Playbooks] --> guard[Deterministic safety guard]
    end

    OBS --> UND --> DEC
    DEC --> rec[("RECORD<br/>decisions.jsonl + incidents.jsonl")]
    rec --> learn[["LEARN<br/>insights.md"]]
```

### OBSERVE — `plato/observe/`
Loads every CSV/JSON file and turns raw, messy signals into a clean, causal view:

- **Quality labels per cell** — `ok`, `missing`, `suspect`, `frozen`, `estimated`, `derived`, `reset`,
  `jitter`, `span`. Blanks stay unknown (never zero); short gaps are carried forward and labelled
  `estimated` (no interpolation); implausible values and frozen sensors are flagged causally; cumulative
  water meters become per-interval use; duplicates and meter exchanges are handled explicitly.
- **Causal as-of view** — `obs.asof(t)` exposes only what was known at time `t` (forecast issue time, tariff
  publication time, roster `status_known_utc`, work-order completion). Detection on the first `k` rows equals
  the first `k` rows of detection on the whole run, so there is no look-ahead.

### UNDERSTAND — `plato/understand/` + `plato/llm/`
Deterministic **causal detectors** each emit time-sorted `Candidate`s with an evidence packet. Candidates are
merged into **episodes** so repeated signals for one incident count as a single detection (and do not become
alert spam). Each episode's evidence packet is then classified into `issue_type` / `likely_cause` / `severity`
/ `confidence` / `reason`:

- With an API key, the **LLM** (`report_incident` tool, temperature 0, optional packet-keyed cache) reads the
  structured evidence — it never writes values into the data and never chooses an action.
- Without a key, or with `--no-llm`, a **rule-based fallback** produces the same shape, keeping the run
  deterministic and offline. Detectors routed as `route="rule"` always use the deterministic path.

### DECIDE & ACT — `plato/decide/`
**Playbooks** map an understood incident to a proposed action, priority, expected outcome and verification
plan. A deterministic **safety guard** (`plato/decide/guard.py`) is the last step before any decision is
written: it enforces the hard never-rules and sets the autonomy / approval / consent flags in plain code, so
neither the LLM nor a playbook can weaken them. Every change the guard makes is recorded as a human-readable
note for the demo. Because the data is a replay, actions are logged, not executed — simulated effects are
labelled as simulated.

### RECORD — `decisions.jsonl` (+ `incidents.jsonl`)
One JSON object per decision following [`DECISION_CONTRACT.md`](DECISION_CONTRACT.md). Required fields:
`event_id`, `timestamp_utc`, `affected_assets`, `issue_type`, `action`, `priority`, `reason`,
`requires_human_approval`. Recommended fields emitted where applicable: `autonomy`, `episode_id`, `evidence`,
`expected_outcome`, `verification_plan`, `dependencies`, `guest_request_ids`, `requires_guest_consent`,
`confidence`. Event IDs are unique; follow-up decisions in an episode depend on the opener. `incidents.jsonl`
is the parallel UNDERSTAND log that the UI links to decisions through `episode_id`.

### LEARN — `insights.md`
The Level 3 feedback: evidence-based recommendations for CLARA (operating rules, thresholds, alarm tuning)
and for PLATO (design of the next building), each with its uncertainty, plus a target architecture.

---

## Scenario coverage

Each scenario class occurs several times a year with varying severity, alongside benign look-alikes and long
stretches of normal operation. Detectors live in `plato/understand/detectors/` and are registered in
`ALL_DETECTORS`.

| Scenario | `issue_type` | Detector | What it does |
|---|---|---|---|
| **S01 Perfect Checkout** | `room_turnaround` | `turnaround` | Early departure + early check-in against a tight cleaning shift; never releases an uncleaned room; gives the guest a realistic ready time. |
| **S02 Hidden Water Leak** | `water_leak` | `water` | Separates real leaks from guest usage, cleaning and meter artefacts using cumulative-meter differences and the main-vs-room balance. |
| **S03 Energy vs Comfort** | `heating_comfort` | `comfort` | Cold snaps + dynamic prices + weak heat pump vs occupied rooms; protects comfort using the forecast rather than blindly minimising cost. |
| **S04 Maintenance Cascade** | `ventilation_maintenance` | `maintenance` | Tells an `AHU1.filter_dp_pa` data gap from true degradation (rising trend in valid data only); times a technician/contractor service to real availability from the roster. |
| **S05 Unexpected Weekend** | `occupancy_surge` | `weekend` | Forecasts occupancy from bookings known so far vs staffed cleaning capacity (cleaners, floor-only robots, on-call housekeeper); never invents capacity and reports the unmet gap. |
| **Cleaning & logistics** | `logistics` | `logistics` | Forecasts waste bin / `WASTE_MAIN` time-to-full and vending stock against pickup/restock schedules; orders extra service early given lead time. |
| **Guest requests** | `guest_request` | `guest` | One decision per request at arrival, in the guest's language. Cleaning is scheduled, faults are inspected (consent if the room is occupied), everything else is a draft reply. A draft is not a sent message, and consent required is not consent granted. Early check-in, waiting and cold-room requests stay with S01 and S03. |
| **Energy optimisation** | `energy_optimization` | `energy` | Shifts battery charge/discharge and pre-heating against day-ahead price, PV and forecast; capped to one proposal/day; savings are simulated estimates only. |
| **Bad-data handling** | `data_quality` | `quality` | Surfaces sensor faults, gaps, frozen sensors and gateway outages as data-quality incidents rather than false alarms. |

---

## Safety & autonomy model

A plain-code guard enforces the CHALLENGE §7 autonomy table on every decision. Autonomy can only ever be
*raised*, never lowered, and guest consent is marked as **needed**, never recorded as granted.

| Autonomy level | Examples | Flags |
|---|---|---|
| **Autonomous** | Schedule cleaning, dispatch robots (floors only), notify guests, adjust occupied-room setpoints inside 20–23 °C, battery/load shifting, order waste pickup or restock, flag anomalies | none |
| **Human approval** | Dispatch technician/contractor, block a room, reassign a booked guest, request an inspection, anything costly | `requires_human_approval = true` |
| **Approval + guest consent** | Entering an occupied room, water shutoff, safety-relevant interventions | `requires_human_approval = true` **and** `requires_guest_consent = true` |

**Hard never-rules enforced in code:**

- Never release a room that is not `clean` → downgraded to `schedule_cleaning`.
- Robots cannot clean bathrooms → `dispatch_robot` becomes staff `schedule_cleaning` when a bathroom is involved.
- Occupied-room setpoints are clamped into 20–23 °C.
- Never trade comfort for energy → `optimize_energy` is converted to `defer_action` when comfort is at risk.
- Low confidence on a costly action → `request_human_approval` instead of acting.
- Unknown actions fall back to `request_human_approval`; invalid priorities fall back to `medium`.
- Simulated effects are labelled as simulated; a proposed guest message is not a delivered one; no fabricated
  execution or savings claims.

Action vocabulary (`plato/decide/vocabulary.py`): `schedule_cleaning`, `dispatch_robot`,
`adjust_room_setpoint`, `optimize_energy`, `block_room`, `release_room`, `reassign_room`,
`request_inspection`, `dispatch_technician`, `order_service`, `notify_guest`, `flag_anomaly`,
`defer_action`, `request_human_approval`.

---

## Data handling & assumptions

- **Blank means missing, never zero.** Missing values stay unknown; only short gaps are carried forward and
  labelled `estimated`. `AHU1.filter_dp_pa` is deliberately never filled.
- **Time.** Timestamps are UTC (ISO 8601); telemetry steps are 15 minutes; operations follow Europe/Vienna
  (DST matters in the dev and holdout years). The UI resolves local wall-clock times to UTC.
- **Cumulative water meters** are converted to per-interval use; resets and meter exchanges are detected and
  excluded rather than read as consumption.
- **Causal, as-of decisions.** Only information available at the decision time is used (forecast `issued_utc`,
  tariff `published_utc`, roster `status_known_utc`, work-order completion).
- **BMS alarms are clues, not ground truth** — delayed, incomplete and sometimes false.
- **No ground truth** is provided. The quickstart (21 days) and dev (365 days) datasets are independent runs
  and are never concatenated; thresholds are baselines learned per dataset rather than constants tuned to one.

---

## Reproducibility & holdout

- **Deterministic by construction.** Detectors and episode grouping are pure; the LLM runs at temperature 0
  with an optional packet-keyed cache; anything the LLM cannot answer falls back to rules. The same inputs
  always produce the same `decisions.jsonl`, with or without an API key.
- **Pre-holdout tag.** The code is tagged `pre-holdout` before the holdout dataset is released, so the jury
  can compare the pre- and post-holdout states.
- **Unchanged holdout run.** The same command is pointed at the holdout dataset and its output is committed as
  `decisions_holdout.jsonl` — the pipeline is not modified for the holdout.
- **Post-tag changes.** The `pre-holdout` tag already includes the Level 1 and Level 2 detectors. After the
  tag, and before seeing any holdout data: the dashboard gained S04, S05 and logistics scenario tabs; turnaround
  decisions also draft a guest-ready time and skip checkouts that still have hours of slack (less alert spam);
  `dispatch_robot` got the same simulated-outcome and verification text as the other actions; `insights.md` /
  `insights.jsonl` record the Level 3 recommendations. None of this changes the one-command interface or how the
  holdout run is produced.

---

## Repository layout

```
run.py                     one-command pipeline -> decisions.jsonl (+ incidents.jsonl)
serve_ui.py                serve the built UI + causal replay API
plato/
  observe/                 OBSERVE: load, clean, quality labels, causal as-of view
  understand/
    detectors/             causal detectors (water, turnaround, comfort, quality, + Level 2)
    episodes.py            group candidates into episodes (one detection per incident)
  llm/                     LLM report_incident tool + rule-based fallback + incident schema
  decide/                  playbooks, deterministic safety guard, action vocabulary
  pipeline.py              end-to-end wiring; ui_payload.py / ui_intelligence.py feed the UI
ui/                        React + Vite command center (build to ui/dist)
tests/                     unittest suite + fake observation fixtures
data/                      dataset + dataset reference (data/README.md)
CHALLENGE.md               the official challenge brief
DECISION_CONTRACT.md       decisions.jsonl schema
decisions_sample.jsonl     illustrative sample (not from any dataset)
```

## Testing

```bash
python3 -m unittest discover -s tests
```

The suite covers OBSERVE quality handling, detectors (including causality and bad-data cases), episode
grouping, the DECIDE guard, the LLM fallback, the pipeline end-to-end and the UI payload.

> Note: pandas can segfault inside the Cursor sandbox in this environment, so run the pipeline and tests in a
> normal shell/virtualenv rather than the sandbox.

## Configuration & secrets

Copy [`.env.example`](.env.example) to `.env` and set `ADAPTION_API_KEY` to enable the LLM. The file is
git-ignored and **contains no secrets** — without a key the pipeline runs fully offline on the rule-based
fallback. Never commit credentials or API keys.

## License

Released under the [MIT License](LICENSE). The PLATO-40 synthetic datasets are provided by the organisers
under CC BY 4.0 ("PLATO-40 synthetic dataset, Proximata Hackathon '26 – Rhomberg track"). *PLATO* and *CLARA*
are Rhomberg product names, used here only to refer to this challenge context.
