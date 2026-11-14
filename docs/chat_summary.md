# PLATO-40 Hackathon: Chat Summary

Summary of the planning and first build session (Fri 13 Nov 2026).
Challenge: **PLATO × CLARA Future, The Autonomous Building Challenge** (Proximata Hackathon '26, Rhomberg track, prize €1,500, team of 2–4).

---

## 1. The challenge in one paragraph

Build a working prototype that runs a fictional 40-room self-check-in hotel (**PLATO-40**) through a replayed year of synthetic data. It must detect problems, decide and act safely (in a mock executor or simulation), verify the outcome, and learn lessons for the operating software (**CLARA**) and for the next building (**PLATO**). Everything concerns the fictional PLATO-40 only. Say "PLATO-40" in the pitch.

## 2. What must be delivered

| Deliverable | Notes |
|---|---|
| **One-command pipeline** | `run ... --data <dir>` reads a dataset and writes `decisions.jsonl`. Must run unchanged on the holdout. |
| **`decisions.jsonl`** | One JSON object per decision (`DECISION_CONTRACT.md`). Required: `event_id`, `timestamp_utc`, `affected_assets`, `issue_type`, `action`, `priority`, `reason`, `requires_human_approval`. Recommended: `autonomy`, `episode_id`, `evidence`, `expected_outcome`, `verification_plan`, `dependencies`, `guest_request_ids`, `requires_guest_consent`, `confidence`. |
| **`decisions_holdout.jsonl`** | Output of the unchanged pipeline on the unseen holdout dataset. |
| **`insights.md`** (optional `insights.jsonl`) | Level 3 lessons for CLARA and PLATO with evidence and uncertainty, plus a target architecture. |
| **Interface / demo** | Shows evidence, risk, decision, autonomy/approval and verification for S01–S03 and at least one feedback recommendation. A dashboard alone or a chat agent alone is not enough. |
| **Repo and video** | Public MIT repo in the Proximata org, runnable README, `.env.example` (no secrets), `LICENSE`, `pre-holdout` git tag, 3-minute demo video, written submission. |

Action vocabulary: `schedule_cleaning`, `dispatch_robot`, `adjust_room_setpoint`, `optimize_energy`, `block_room`, `release_room`, `reassign_room`, `request_inspection`, `dispatch_technician`, `order_service`, `notify_guest`, `flag_anomaly`, `defer_action`, `request_human_approval`.

## 3. Scenarios

- **Level 1 (mandatory)**
  - **S01 Perfect Checkout:** early departure, early check-in request and a tight cleaning shift. Never release an uncleaned room, and give the guest a realistic time.
  - **S02 Hidden Water Leak:** loud, subtle, or only visible between the main and room meters. Separate it from guests, cleaning and meter artefacts.
  - **S03 Energy vs Comfort:** cold weather, dynamic prices and a weak heat pump meet occupied rooms. Protect comfort, use the forecast.
- **Level 2 (advanced)**
  - **S04 Maintenance Cascade:** filter degradation, missing sensor data and an absent technician.
  - **S05 Unexpected Weekend:** an event weekend and a sick cleaner.
  - Building and energy optimisation (prices, PV, battery) and cleaning and logistics (robots, waste, vending).
- **Level 3 (learn and feed back):** what CLARA should do differently, what PLATO should build differently (year-long evidence), and a target architecture.

## 4. Rules that matter

**Data rules**
- Blank means missing, never zero. Expect implausible values, frozen sensors, duplicates and meter exchanges.
- Timestamps are UTC, operations run on Europe/Vienna (DST matters in the dev and holdout years).
- Water meters are cumulative, so use differences.
- BMS alarms are delayed, incomplete and sometimes false: a clue, not ground truth.
- **Only use information known at that moment** (forecast `issued_utc`, tariff `published_utc`, roster `status_known_utc`).
- No ground truth is provided. The quickstart (21 days) and dev (365 days) datasets are independent runs: do not concatenate.

**Autonomy rules**

| Level | Examples | Flags |
|---|---|---|
| Autonomous | schedule cleaning, dispatch robots, notify guests, adjust setpoints (occupied rooms inside 20–23 °C), battery shifting, order waste pickup or restock, flag anomalies | none |
| Human approval | dispatch technician or contractor, block a room, move a booked guest, anything costly | `requires_human_approval = true` |
| Approval and guest consent | enter an occupied room, water shutoff, safety interventions | both `requires_human_approval` and `requires_guest_consent` true. Consent is *needed*, never assumed granted. |

**Hard "never" rules:** never release an uncleaned room; robots cannot clean bathrooms, certify or release a room; never schedule absent staff or robots; never trade safety or comfort for energy; simulated effects are not measurements and a proposed message is not a delivered message; no fabricated execution or savings claims.

## 5. Scoring (100 points)

| Category | Points |
|---|---|
| Technical implementation (reproducible pipeline, detection on dev and holdout, prioritisation, robustness to bad data) | 35 |
| Autonomous operation and impact (safe autonomy, coordination, guest communication, energy/comfort trade-offs) | 30 |
| Learning loop and vision (feedback to CLARA and PLATO, target architecture) | 15 |
| Demo and presentation | 20 |

Repeated alerts for one incident count as one detection, alert spam counts negatively, and false alarms are judged across normal operation. An automated check gives the jury input only.

## 6. Dates

- **Sat 14 Nov ~11:00:** holdout dataset released on Discord. Tag the code `pre-holdout` **before** this.
- **Sat 14 Nov 14:00 sharp (CHALLENGE.md):** code freeze. Commit `decisions_holdout.jsonl` before it. The Luma event page says 15:00, so trust the 14:00 in CHALLENGE.md.
- **15:00–16:00:** live-demo judging. The track winner pitches in the final round.
- Changes after `pre-holdout` must be justified in the README.

## 7. The data (quickstart, 5–25 Jan 2026)

| File | Rows | Note |
|---|---|---|
| `room_telemetry.csv` | 80,642 | 40 rooms × 15 min; temperature, setpoint, humidity, CO₂, presence, window, housekeeping state |
| `room_water.csv` / `water_main.csv` | 80,640 / 2,016 | cumulative meters |
| `energy.csv`, `weather.csv`, `weather_forecast.csv`, `tariff.csv` | 2,016 / 2,016 / 972 / 504 | |
| `equipment.csv` | 16,128 | HP1 (heat pump) and AHU1 (air handling unit), long format |
| `logistics.csv`, `staff_roster.csv`, `work_orders.csv`, `guest_requests.csv` | 3,528 / 102 / 9 / 112 | |
| `bookings.csv`, `access_log.csv`, `events.jsonl` | 281 / 3,787 / 1,406 | |

**Hints found in the quickstart (my readings, not confirmed answers)**
- **S01:** `CLEANER_1` is sick on Jan 8 and Jan 24. There are 6 "waiting for room" guest requests on Jan 24, plus 2 on-call housekeeping calls.
- **S02:** R104 shows steady ~25–28 L per 15 min until the burst hose was repaired (`WO00003`, Jan 13). R305 matches a continuously running toilet valve (`WO00009`, Jan 25). R403's water meter was exchanged on Jan 18 (`WO00004`).
- **S03:** many `temperature_c` cold requests on Jan 10, 12, 15–18 and 23–25, all needing guest consent. Possible causes: HP1 refrigerant leak serviced Jan 17–18 (`WO00007`), unplanned AHU1 filter replacement Jan 23 (`WO00008`). HP1 also reports 5 steps of `lockout_high_pressure`.
- **S04:** `TECH_1` was sick on Jan 15 and on vacation Jan 19–23. `AHU1.filter_dp_pa` has a 404-step gap (Jan 16 22:30 – Jan 21 03:15) in the middle of a rising trend.
- **Bad data:** gaps, a 85.0 °C reading in R106, a frozen R205 sensor, two duplicate rows, floor-3 gateway outages and `SENSOR_COMM_LOSS` alarms.

**Correction made during the chat:** an early count wrongly said only `room_telemetry.csv` had blanks. The recount shows blanks in three files: `room_telemetry` (2,732 across 7 columns), `room_water` (368) and `equipment` (419).

## 8. Tools mentioned (hackathon sponsors)

| Tool | What it is | Use here |
|---|---|---|
| **n8n** | Visual workflow automation with AI nodes | Useful as a visible mock executor or action layer. Keep core detection in code. |
| **Lovable** | AI app builder from prompts | Useful for the dashboard or UI. |
| **Maritime** (YC F26) | Cloud for AI agents (serverless containers with memory, triggers, channels) | Optional hosting for agents. The one-command run must not depend on it. |
| **Biscuit** (biscuit.so) | AI platform that builds internal tools from a prompt, with database, email and hosting. Sources disagree on its description, so check at their booth. | Optional, for a dashboard or ops tool. |
| **Adaption** (Adaption Labs) | Platform and Python SDK to improve, translate and generate AI training datasets | Low relevance (maybe multilingual guest-message samples). An n8n + Adaption workshop is Sat 11:00, the same time as the holdout release. |

None of the tools is required. The jury rewards grounded, working results.

## 9. Approach discussed

**Pipeline shape**

```
data ─► OBSERVE (load, clean, label quality)
     ─► UNDERSTAND (detect problems, explain evidence)
     ─► DECIDE & ACT (choose actions, check rules, mock-execute)
     ─► RECORD (decisions.jsonl, audit trail) ─► LEARN (insights.md)
```

**Options for Understand:** rules and thresholds, statistics against a per-asset baseline, machine learning (little data, no labels), or an LLM reading structured evidence.
**Options for Decide & act:** rule-based playbooks, optimisation/scheduling, LLM agents with tools, a workflow tool (n8n) for execution. Whichever is chosen, a deterministic **safety guard** at the end enforces the rules and the approval/consent flags.

**User choices so far:** Python, team of 3, ambition "everything", and an LLM as a core part. Suggested way to combine an LLM with reproducibility: deterministic detectors produce evidence, the LLM reasons over candidate incidents through tools, a plain-code safety guard checks every decision, and a rule-based fallback keeps the one-command run working without an API key.
**Not yet decided:** which concrete options to use for Understand and Decide & act.

**Suggested split for three people:** (A) data and detectors, (B) agents, planning, safety guard and mock executor, (C) insights, UI, demo and README.

**Level 3 hypotheses to test on the dev year:** no cooling (summer overheating by orientation and floor), heat-pump capacity in cold snaps, waste container size on event weekends, single technician as a single point of failure, robots unable to clean bathrooms, sensor dropouts by gateway, BMS alarm timing and false alarms, filter wear against its planned interval.

## 10. Built so far: the OBSERVE stage

**Design decision:** data-quality checks and gap handling are plain, deterministic code (no LLM). An LLM could later explain findings or read terse work-order text, but it never writes values into the data.

| Handling | What it does | Quickstart result |
|---|---|---|
| Blanks | Stay unknown, never 0. Short gaps carried forward and labelled `estimated` (1 h temperature/humidity, 30 min CO₂, 2 h setpoint). No interpolation. Filter pressure is never filled. | 473 blank temperatures became 229 unknown + 244 estimated |
| Implausible values | Range and jump checks, set to unknown | R106 at 85.0 °C |
| Frozen sensor | Flagged only once the run reached 8 repeats (causal) | R205, 85 steps |
| Duplicates | Dropped and logged | 2 exact copies |
| Gateway outages | A floor "goes dark" when most rooms lose all key sensors together | GW_F3 twice on Jan 11 |
| Water meters | Cumulative → per-interval use; handles blanks (span), jitter and resets | R403 reset (27,020.8 → 0.4 L) matched to `WO00004`, 337 jitter steps |
| Housekeeping state | Blanks filled from cleaning events, labelled `derived` | events agree with telemetry on 100% of 72,469 cells; 210 filled |
| Occupancy | Rebuilt from stay events, separate from motion | motion 39.3% in a stay, 1.9% when vacant |
| Balances | Energy and water balance as cross-checks | energy residual 0; water common-area residual median ~3.6 L / 15 min |
| As-of view | `obs.asof(t)` shows only what was known at t (forecast issue time, tariff publication, roster known-time, work-order completion) | tested |

**Quality labels per cell:** ok, missing, suspect, frozen, estimated, derived, reset, jitter, span.

**A bug found by a test:** the jump check flagged the reading right after a spike. It now compares against the last *good* reading (kept for 4 steps).

**Tests:** 16, all passing, including a check that cleaning is causal (cleaning the first k rows equals the first k rows of cleaning everything). The stage also tolerates missing optional files.

**Known limits**
- Thresholds are generic physical constants, not tuned on one dataset.
- `bookings.csv` has no cancellation timestamp (flagged `cancel_time_unknown`).
- The event type `room_reassigned` is not in the quickstart, so it is unused. The report lists event types not used for room state.
- Pandas segfaults inside Cursor's sandbox in this environment, so the pipeline was run outside it.

## 11. Repository

- Local folder: `/Users/timur/Downloads/plato40_hackathon26` (renamed from `PLATO40_v2_quickstart`).
- GitHub: `https://github.com/timuriz/plato40_hackathon26`, **private**, branch `main`, one commit (`d4b56dd`).
- Layout: `data/` (all dataset files plus the dataset reference as `data/README.md`), `plato/observe/` (the stage), `tests/test_observe.py`, `run.py`, `requirements.txt`, `.gitignore`, `CHALLENGE.md`, `DECISION_CONTRACT.md`, the two sample files, `docs/`.
- Run: `python3 run.py --data data` (add `--report-json out/observe_report.json`). Tests: `python3 -m unittest discover -s tests`.

## 12. Next steps

1. **Decide the tools** for Understand and Decide & act (see section 9).
2. **Build Understand and Decide & act for S01–S03** on top of the observe stage: detectors with evidence, the autonomy and approval flags, the safety guard, then episode deduplication and a valid `decisions.jsonl`.
3. **Run on the 365-day dev set often**, not only the quickstart. Prefer baselines computed from each dataset over hard-coded thresholds.
4. **Add** S04, S05, logistics and energy optimisation, then the Level 3 insights and target architecture, then the UI and demo video.
5. **Before the holdout (Sat ~11:00):** freeze and tag `pre-holdout`. Run the pipeline unchanged on the holdout and commit `decisions_holdout.jsonl` before 14:00.
6. **Repository chores:** root README (setup, run command, architecture, assumptions), `.env.example`, MIT `LICENSE` (copyright holder needed), make the repo public in the Proximata org when the organisers assign the path, and reconsider whether to ship `data/` (the challenge prefers loading it via script).

## 13. Open questions

- Which options for Understand and Decide & act? Is the LLM a core decision maker with a safety guard and fallback, as suggested?
- Who on the team takes which part, and does anyone want optimisation or scheduling code?
- Should the demo show a visible agent executing (n8n) or mainly a clear dashboard?
- Copyright holder name for the MIT licence, and the Proximata repo path.
