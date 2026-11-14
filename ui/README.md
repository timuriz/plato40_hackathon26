# PLATO-40 Command Center UI

Eigenständige React-Oberfläche mit Vite. Die UI ersetzt nicht `dashboard/`, führt keine physischen Aktionen aus und liest Replay-Daten aus der vorhandenen kausalen `observe.asof(t)` Pipeline.

## Build und Start

```bash
cd /Users/admin/Documents/plato40_hackathon26
npm run install:ui   # einmalig
npm run build
python serve_ui.py --data data
```

Dann `http://127.0.0.1:8090` öffnen. Vite erzeugt `ui/dist`; der Python-Server liefert diesen Build aus und stellt die Replay-API bereit.

Für die lokale UI-Entwicklung zwei Terminals verwenden:

```bash
# Terminal 1 – API und Dataset
python serve_ui.py --data data

# Terminal 2 – Vite mit Hot Reload
npm run dev
```

Die Vite-Entwicklungsoberfläche läuft auf `http://127.0.0.1:5173` und proxyt `/api` an den Python-Server auf Port 8090.

Pipeline-Ausgaben können so angeschlossen werden:

```bash
python serve_ui.py --data data --incidents outputs/incidents.jsonl --decisions outputs/decisions.jsonl
```

## Datenvertrag

- `GET /api/health`: Pipelineprofil, Replay-Grenzen und Anzahl Zeitpunkte.
- `GET /api/dashboard?at=<ISO-8601 UTC>`: Zustand am letzten realen Dataset-Zeitpunkt bis einschließlich `at`. Serverseitig wird `obs.asof(t)` verwendet.
- `GET /api/room-history?at=<ISO-8601 UTC>&room_id=R101`: kausale Stundenwerte für Temperatur, Sollwert, Feuchte und CO₂ des ausgewählten Zimmers.
- `--incidents`: optionales JSONL der Detektion. Eine Zeile pro Episode/Datensatz, Zeitfeld `timestamp_utc`; sinnvolle Felder sind `episode_id`, `event_id`, `issue_type`, `priority`, `status`, `affected_assets`, `title`.
- `--decisions`: optionales JSONL des Entscheidungsmodells. Die Felder aus `DECISION_CONTRACT.md` werden direkt angezeigt: `event_id`, `timestamp_utc`, `affected_assets`, `issue_type`, `action`, `priority`, `reason`, `requires_human_approval` und optionale Erklärungs-/Verifikationsfelder.

Die beiden Logs werden nur bis zum ausgewählten Replay-Zeitpunkt angezeigt. Ein nicht übergebener oder nicht existierender Logpfad wird als nicht verfügbar dargestellt. Ein vorhandener, aber leerer Log wird als verbundene Quelle ohne Einträge dargestellt.

Die 3D-Ansicht erzeugt ihre einzelnen Zimmer aus `building.json` (Etage, Orientierung und Fläche). Das ist ausdrücklich eine schematische Geometrie, kein vermessener Bauplan. Belegung, Housekeeping und Sensorwerte kommen zum gewählten Replay-Zeitpunkt aus der bestehenden Pipeline; Incidents werden nur für reale JSONL-Einträge markiert. Bei nicht verfügbarem WebGL zeigt die Oberfläche einen interaktiven 2D-Grundriss als Fallback.

Im 3D-Modell: links ziehen zum Drehen, Mausrad oder Zoom-Tasten zum Vergrößern/Verkleinern, rechts ziehen zum Verschieben. „Reset camera“ stellt die Gebäudeübersicht wieder her; beim Zimmerklick fährt die Kamera weich zum Zimmer und gibt danach die freie Steuerung zurück.

## Pipeline-Integration

Der aktuelle `run.py` führt nur die Beobachtungs- und Qualitätsstufe aus. Er erzeugt momentan keine LLM-Entscheidungen und keine Incident-Episoden. Das LLM bzw. ein nachgelagerter Pipeline-Schritt kann die strukturierten JSONL-Ausgaben schreiben; anschließend werden deren Pfade mit `--decisions` und `--incidents` übergeben. React bleibt dabei vom konkreten Modell entkoppelt und rendert den vereinbarten Datenvertrag.

Werte, die im ausgewählten Zeitpunkt unbekannt oder nicht angeschlossen sind, bleiben als unbekannt bzw. `—` sichtbar. Die UI erfindet keine Vorfälle, Empfehlungen, Entscheidungen, Erfolgsquoten oder Jahres-Insights. Eine Approval-Schaltfläche oder Aktor-Ausführung ist nicht angeschlossen.
