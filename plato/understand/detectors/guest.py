"""One decision per guest request, at the moment it arrives.

early_checkin, waiting_for_room and temperature_cold are left to the turnaround and comfort detectors.
Everything else gets a fixed draft in the guest's language. The draft is a proposal: the data never
contains a sent reply. If the reply deadline is still inside the dataset and the first priority was
only low or medium, one follow-up raises it. Consent is required only when the request says so and
the room is occupied at that time. It is never recorded as granted.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..candidate import GUEST, Candidate

# Already turned into cleaning / comfort decisions elsewhere.
SKIP = {"early_checkin", "waiting_for_room", "temperature_cold"}
INSPECT = {"water_issue", "maintenance_issue"}
RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Short drafts. Missing language falls back to English. None of these is a delivered message.
_DRAFTS = {
    "extra_towels": {
        "en": "We will bring extra towels to your room.",
        "de": "Wir bringen zusätzliche Handtücher auf Ihr Zimmer.",
        "it": "Portiamo asciugamani in più in camera.",
        "fr": "Nous apportons des serviettes supplémentaires dans votre chambre.",
        "es": "Llevamos toallas extra a su habitación.",
        "nl": "We brengen extra handdoeken naar uw kamer.",
    },
    "wifi_help": {
        "en": "The lobby Wi-Fi name and password are on the key card. We can resend them.",
        "de": "WLAN-Name und Passwort stehen auf der Schlüsselkarte. Wir senden sie gern noch einmal.",
        "it": "Nome e password del Wi-Fi sono sulla chiave. Possiamo reinviarli.",
        "fr": "Le nom et le mot de passe du Wi-Fi sont sur la carte. Nous pouvons les renvoyer.",
        "es": "El nombre y la clave del Wi-Fi están en la tarjeta. Podemos reenviarlos.",
        "nl": "Wifi-naam en wachtwoord staan op de sleutelkaart. We sturen ze graag opnieuw.",
    },
    "noise_complaint": {
        "en": "Thank you, we have noted the noise and will check the floor.",
        "de": "Danke, wir haben den Lärm notiert und prüfen die Etage.",
        "it": "Grazie, abbiamo annotato il rumore e controlliamo il piano.",
        "fr": "Merci, nous notons le bruit et vérifions l'étage.",
        "es": "Gracias, anotamos el ruido y revisamos la planta.",
        "nl": "Dank, we noteren het geluid en controleren de verdieping.",
    },
    "parking": {
        "en": "Guest parking is in the garage under the building. We will send the entrance note.",
        "de": "Das Gästeparken ist in der Tiefgarage. Wir senden die Einfahrtshinweise.",
        "it": "Il parcheggio ospiti è nel garage sotto l'edificio. Inviamo le indicazioni.",
        "fr": "Le parking clients est au sous-sol. Nous envoyons l'accès.",
        "es": "El parking de clientes está en el garaje. Enviamos el acceso.",
        "nl": "De gastenparking is in de garage onder het gebouw. We sturen de toegang.",
    },
    "luggage_storage": {
        "en": "We can keep your luggage at reception.",
        "de": "Wir können Ihr Gepäck an der Rezeption aufbewahren.",
        "it": "Possiamo tenere i bagagli alla reception.",
        "fr": "Nous pouvons garder vos bagages à la réception.",
        "es": "Podemos guardar su equipaje en recepción.",
        "nl": "We kunnen uw bagage aan de receptie bewaren.",
    },
    "invoice": {
        "en": "We will prepare the invoice and send it to the address you gave.",
        "de": "Wir stellen die Rechnung aus und senden sie an die genannte Adresse.",
        "it": "Prepariamo la fattura e la inviamo all'indirizzo indicato.",
        "fr": "Nous préparons la facture et l'envoyons à l'adresse indiquée.",
        "es": "Preparamos la factura y la enviamos a la dirección indicada.",
        "nl": "We maken de factuur en sturen die naar het opgegeven adres.",
    },
    "air_quality": {
        "en": "Thank you, we will check the air in your room.",
        "de": "Danke, wir prüfen die Luft in Ihrem Zimmer.",
        "it": "Grazie, controlliamo l'aria in camera.",
        "fr": "Merci, nous vérifions l'air de votre chambre.",
        "es": "Gracias, revisamos el aire de su habitación.",
        "nl": "Dank, we controleren de lucht in uw kamer.",
    },
    "arrival_delay": {
        "en": "Thank you for the update. Your room stays reserved.",
        "de": "Danke für die Nachricht. Ihr Zimmer bleibt reserviert.",
        "it": "Grazie per l'aggiornamento. La camera resta riservata.",
        "fr": "Merci pour l'information. Votre chambre reste réservée.",
        "es": "Gracias por avisar. Su habitación sigue reservada.",
        "nl": "Dank voor het bericht. Uw kamer blijft gereserveerd.",
    },
    "late_checkout": {
        "en": "A later checkout is not confirmed yet. Reception will reply with a time we can keep.",
        "de": "Ein späterer Check-out ist noch nicht bestätigt. Die Rezeption nennt eine haltbare Zeit.",
        "it": "Il late check-out non è ancora confermato. La reception risponde con un orario reale.",
        "fr": "Le départ tardif n'est pas encore confirmé. La réception répondra avec une heure tenable.",
        "es": "El late check-out aún no está confirmado. Recepción responderá con una hora real.",
        "nl": "Een latere check-out is nog niet bevestigd. De receptie antwoordt met een haalbare tijd.",
    },
    "cleaning_request": {
        "en": "We have scheduled cleaning for your room.",
        "de": "Wir haben die Reinigung für Ihr Zimmer eingeplant.",
        "it": "Abbiamo programmato le pulizie della camera.",
        "fr": "Nous avons planifié le ménage de votre chambre.",
        "es": "Hemos programado la limpieza de su habitación.",
        "nl": "We hebben de schoonmaak van uw kamer ingepland.",
    },
    "water_issue": {
        "en": "Thank you, we will inspect the water issue. We will ask before entering if you are in the room.",
        "de": "Danke, wir prüfen das Wasserproblem. Wenn Sie im Zimmer sind, fragen wir vor dem Eintreten.",
        "it": "Grazie, controlliamo l'acqua. Se siete in camera, chiediamo prima di entrare.",
        "fr": "Merci, nous vérifions l'eau. Si vous êtes dans la chambre, nous demandons avant d'entrer.",
        "es": "Gracias, revisamos el agua. Si está en la habitación, pedimos permiso antes de entrar.",
        "nl": "Dank, we controleren het water. Als u op de kamer bent, vragen we voor we binnenkomen.",
    },
    "maintenance_issue": {
        "en": "Thank you, we will inspect the fault. We will ask before entering if you are in the room.",
        "de": "Danke, wir prüfen den Mangel. Wenn Sie im Zimmer sind, fragen wir vor dem Eintreten.",
        "it": "Grazie, controlliamo il guasto. Se siete in camera, chiediamo prima di entrare.",
        "fr": "Merci, nous vérifions la panne. Si vous êtes dans la chambre, nous demandons avant d'entrer.",
        "es": "Gracias, revisamos la avería. Si está en la habitación, pedimos permiso antes de entrar.",
        "nl": "Dank, we controleren het defect. Als u op de kamer bent, vragen we voor we binnenkomen.",
    },
    "other": {
        "en": "Thank you, we have received your message and will reply.",
        "de": "Danke, wir haben Ihre Nachricht erhalten und antworten.",
        "it": "Grazie, abbiamo ricevuto il messaggio e rispondiamo.",
        "fr": "Merci, nous avons reçu votre message et nous répondons.",
        "es": "Gracias, hemos recibido su mensaje y respondemos.",
        "nl": "Dank, we hebben uw bericht ontvangen en antwoorden.",
    },
}


def draft_message(kind: str, language: str) -> str:
    table = _DRAFTS.get(kind) or _DRAFTS["other"]
    return table.get(str(language or "en").lower()) or table["en"]


def _priority(raw: str, overdue: bool) -> str:
    base = raw if raw in RANK else "medium"
    if overdue and RANK[base] < RANK["high"]:
        return "high"
    return base


def _occupied(obs, room: str, t: pd.Timestamp) -> bool:
    occ = getattr(getattr(obs, "rooms", None), "occupied", None)
    if occ is None or room not in getattr(occ, "columns", []):
        return False
    col = occ[room]
    pos = col.index.searchsorted(pd.Timestamp(t), side="right") - 1
    if pos < 0:
        return False
    return bool(col.iloc[pos])


def _ts(value) -> Optional[pd.Timestamp]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    t = pd.Timestamp(value)
    if pd.isna(t):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _candidate(row, t: pd.Timestamp, overdue: bool, occupied: bool) -> Candidate:
    kind = str(row.get("request_type") or "other")
    lang = str(row.get("language") or "en")
    room = str(row.get("room_id") or "BUILDING")
    rid = str(row.get("request_id") or "")
    consent = str(row.get("requires_guest_consent") or "") == "required"
    pri = _priority(str(row.get("priority") or "medium").lower(), overdue)
    msg = draft_message(kind, lang)
    if overdue:
        msg = "Sorry this reply is late. " + msg
    due = _ts(row.get("response_due_utc"))
    feats = {
        "episode_asset": rid or room,
        "request_id": rid,
        "request_type": kind,
        "language": lang.lower(),
        "priority": pri,
        "response_due_utc": due.strftime("%Y-%m-%dT%H:%M:%SZ") if due is not None else None,
        "overdue": overdue,
        "occupied": occupied,
        "consent_required": consent and occupied,
        "draft_message": msg,
    }
    return Candidate(
        detector=GUEST, asset=room if room and room != "nan" else "BUILDING", t=t,
        severity_hint=pri, features=feats, affected_assets=[room],
        guest_request_ids=[rid] if rid else [],
        requires_guest_consent=bool(consent and occupied),
        route="rule",
    )


def detect_guest_requests(obs) -> List[Candidate]:
    frame = getattr(obs, "guest_requests", None)
    if frame is None or len(frame) == 0 or "timestamp_utc" not in frame.columns:
        return []
    end = obs.grid[-1] if len(getattr(obs, "grid", [])) else None
    out: List[Candidate] = []
    for row in frame.to_dict("records"):
        kind = str(row.get("request_type") or "")
        if kind in SKIP:
            continue
        t = _ts(row.get("timestamp_utc"))
        if t is None:
            continue
        room = str(row.get("room_id") or "BUILDING")
        occupied = _occupied(obs, room, t)
        first = _candidate(row, t, overdue=False, occupied=occupied)
        out.append(first)
        due = _ts(row.get("response_due_utc"))
        opened = str(row.get("priority") or "medium").lower()
        if due is None or due <= t or RANK.get(opened, 1) >= RANK["high"]:
            continue
        if end is not None and due > end:
            continue
        out.append(_candidate(row, due, overdue=True, occupied=_occupied(obs, room, due)))
    out.sort(key=lambda c: (c.t, c.features.get("request_id", ""), c.features.get("overdue", False)))
    return out
