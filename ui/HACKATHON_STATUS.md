# Dashboard and challenge implementation audit

Status is for this checkout and supplied integrations; a UI display alone does not establish that an
operational workflow was performed. `Complete` means the stated display/control is implemented, not that
all challenge deliverables or detection accuracy are proven.

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Building overview and clickable rooms | Complete | 3D schematic, occupancy, reported housekeeping state, sensor quality and room inspector. Not a surveyed plan. |
| Incident, decision and approval visibility | Partial | Supplied detector/decision logs are linked by episode; unreported status remains unknown. Actual approval store and actions do not exist. |
| Staff and robot overview | Partial | Scheduled shifts and latest recorded activity/access are shown. Location and availability are not live; the roster does not prove an unassigned person is free. |
| Historical replay, play/pause, speed, local time, reset, incident navigation | Complete | Causal as-of endpoint, Europe/Vienna display, datetime navigation with DST validation, and next/previous incident timestamps. |
| S01 turnaround investigation | Partial | Actual detector findings, reported readiness, known staffing, historical events and linked proposals are displayed. Full feasible assignment and delivered guest notifications need an executor and notification audit. |
| S02 water investigation | Partial | Actual detected suspicions, meters, incomplete balance and quality context are displayed. Benign explanations are prompts to investigate, not confirmed root causes. |
| S03 comfort / energy investigation | Partial | Detected issues, room/plant measurements, tariff and known forecasts are displayed. Evaluation of actions and outcomes needs an executor. |
| Incident evidence, risk, decisions, approval and verification | Partial | Detail fields render when the detector/decision records supply them; missing impact, alternatives and proof are explicitly unavailable. |
| Explainable decision center and official contract | Complete | Displays required and recommended decision fields, with expandable raw record and separate lifecycle stages. |
| Approval/consent interaction and safety | Missing | No approval or consent store, write endpoint or execution-time validation. Existing guard operates on decision packets; it is not an executor or resource reservation system. |
| Mock action execution and coordination | Missing | No mock executor, resource locking, effect ledger, or actual action outcome is connected. Imported action audit records can be displayed read-only. |
| Data quality and uncertainty | Partial | Current room quality codes and time-visible pipeline findings are shown; some untimestamped ingestion findings and alarm reliability need separate causal diagnostics. |
| Evidence-based CLARA and PLATO recommendations | Partial | Sensor-continuity recommendations derive from known historical equipment-quality samples; this quickstart is only 21 days and does not establish a yearly performance finding. Timestamped external insights are supported. |
| Target architecture | Complete | Proposed data → detection → LLM → guard → approvals → mock executor → verification path is shown and labelled as future architecture. |
| Three-minute connected incident-to-decision demo | Partial | A real pipeline run can populate records and the UI links them. Reproducible demo timing, S01–S03 accuracy validation, approval, execution and verified outcome are not established. |
| Holdout / final challenge submission | Missing | Not established by this UI: tagged pre-holdout state, independent holdout run, full-year insights, submission and evaluation. |

To reproduce the local offline UI data:

```bash
python3 run.py --data data --no-llm --out out/decisions.jsonl
npm run build
python3 serve_ui.py --data data --decisions out/decisions.jsonl --incidents out/incidents.jsonl
```

`--no-llm` uses the deterministic fallback, not a live LLM. Pipeline output is a set of detected candidates
and guarded proposals, not confirmed faults, executed actions, delivered messages, or verified outcomes.
Validate detection precision and causality on dev/holdout before making performance claims.
