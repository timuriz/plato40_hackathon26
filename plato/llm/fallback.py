"""Deterministic rule-based UNDERSTAND fallback: used when there is no API key, the LLM output stays invalid
after one retry, or the call fails. It reads the same evidence packet and returns the same shape as the LLM
(`validate_incident` passes). Like the LLM it only classifies; it never picks an action (that is DECIDE).

Packet convention (built by the detectors, see PACKETS in scripts/llm_probe.py):
    {"detector": "water_baseline" | "comfort" | "sensor_quality" | "room_turnaround" | ..., "asset": "R104",
     "features": {...numbers/flags...}, "quality_flags": [...]}
Unknown detectors fall back to a low-confidence `data_quality` finding.
"""
from __future__ import annotations

from typing import Any, Dict


def _out(issue_type, cause, severity, conf, reason, used) -> Dict[str, Any]:
    return {"issue_type": issue_type, "likely_cause": cause, "severity": severity,
            "confidence": conf, "reason": reason, "evidence_used": list(used)}


def rule_based_incident(packet: Dict[str, Any]) -> Dict[str, Any]:
    det = packet.get("detector", "")
    f = packet.get("features", {}) or {}
    flags = set(packet.get("quality_flags", []) or [])
    asset = packet.get("asset", "unknown asset")

    if det == "water_baseline":
        if "reset" in flags or f.get("meter_reset_in_window"):
            return _out("data_quality", "meter_artefact", "low", 0.7,
                        f"{asset}: the jump coincides with a meter counter reset, not a real flow.",
                        ["quality_flags", "meter_reset_in_window"])
        vacant = f.get("occupied_share", 1.0) <= 0.05
        steady = f.get("flow_cv", 9.9) <= 0.3 and f.get("duration_h", 0) >= 2
        high = f.get("flow_z", 0) >= 4
        if vacant and steady and high:
            return _out("water_leak", "equipment_fault", "high", 0.75,
                        f"{asset}: steady high flow in a vacant room, far above its own baseline "
                        f"(z={f.get('flow_z')}) for {f.get('duration_h')} h.",
                        ["occupied_share", "flow_cv", "flow_z", "duration_h"])
        if f.get("occupied_share", 0) > 0.5 and f.get("duration_h", 0) < 1:
            return _out("no_incident", "guest_usage", "low", 0.7,
                        f"{asset}: short burst while occupied, consistent with guest use.",
                        ["occupied_share", "duration_h"])
        return _out("water_leak", "unknown", "medium", 0.4,
                    f"{asset}: unusual water use but the evidence is not conclusive.",
                    ["flow_z", "occupied_share"])

    if det == "comfort":
        # defrost is a normal heat-pump state; only lockouts / unknown codes count as a fault
        hp_bad = bool(f.get("hp_alarm")) or any(
            k not in ("running", "standby", "defrost") for k in (f.get("hp_status_counts_24h") or {}))
        if f.get("rooms_cold_now", 0) and f["rooms_cold_now"] >= 3:
            return _out("heating_comfort", "equipment_fault", "high", 0.7,
                        f"{asset}: {f['rooms_cold_now']} occupied rooms are below the comfort floor at once, "
                        "which points to a heating-plant problem.",
                        ["rooms_cold_now", "hp_status_counts_24h", "outdoor_c"])
        cold = f.get("temp_c") is not None and f.get("temp_c") < 20
        if cold:
            cause = "equipment_fault" if hp_bad else "weather_load"
            sev = "high" if f.get("guest_cold_requests", 0) > 0 else "medium"
            return _out("heating_comfort", cause, sev,
                        0.7 if f.get("guest_cold_requests", 0) > 0 else 0.55,
                        f"{asset}: occupied room below the comfort floor"
                        + (" with a guest cold request" if f.get("guest_cold_requests", 0) > 0 else "") + ".",
                        ["temp_c", "guest_cold_requests", "hp_status_counts_24h"])
        return _out("no_incident", "unknown", "low", 0.5,
                    f"{asset}: comfort is inside the allowed band.", ["temp_c"])

    if det == "sensor_quality":
        if "reset" in flags or f.get("meter_reset_in_window"):
            wo = f.get("confirmed_by_work_order")
            return _out("data_quality", "meter_artefact", "low", 0.8,
                        f"{asset}: cumulative meter was exchanged (counter restart)"
                        + (f", confirmed by {wo}" if wo else "") + "; not a real water event.",
                        ["quality_flags", "meter_reset_in_window", "confirmed_by_work_order"])
        return _out("data_quality", "sensor_fault", "low", 0.7,
                    f"{asset}: sensor looks faulty ({', '.join(sorted(flags)) or 'quality flags'}).",
                    ["quality_flags"])

    if det == "room_turnaround":
        state = f.get("housekeeping_state")
        waiting = bool(f.get("guest_waiting"))
        slack = f.get("slack_minutes")
        sick = f.get("cleaners_sick_known") or []
        ready = f.get("projected_ready_utc")
        if state == "clean" and not waiting:
            return _out("room_turnaround", "unknown", "low", 0.6,
                        f"{asset}: room is clean and ready for the next guest.", ["housekeeping_state"])
        cause = "staffing"
        late = slack is not None and slack < 0
        if waiting or late:
            sev, conf = "high", 0.8
        elif slack is not None and slack < 60:
            sev, conf = "medium", 0.75
        else:
            sev, conf = "low", 0.7
        reason = (f"{asset}: room is {state or 'not clean'} and the next guest "
                  + ("is already waiting" if waiting else "arrives soon")
                  + (f"; projected ready {ready}" if ready else "")
                  + (f", {abs(slack):.0f} min late" if late else "")
                  + (f"; {len(sick)} cleaner(s) called in sick" if sick else "") + ".")
        return _out("room_turnaround", cause, sev, conf, reason,
                    ["housekeeping_state", "guest_waiting", "slack_minutes", "projected_ready_utc",
                     "cleaners_sick_known"])

    if det == "energy_optimization":
        bp, ph = f.get("battery_plan"), f.get("preheat_plan")
        spread = f.get("price_spread_eur_kwh")
        if not (bp or ph) or spread is None:
            return _out("no_incident", "unknown", "low", 0.5,
                        f"{asset}: no meaningful price spread to shift load, nothing to optimise.",
                        ["price_spread_eur_kwh"])
        parts = []
        if bp:
            parts.append(f"charge the battery in cheap hours and discharge in expensive ones "
                         f"(spread {spread:.3f} EUR/kWh"
                         + (f"; simulated saving about {f['simulated_saving_eur']:.2f} EUR, not a measurement"
                            if f.get("simulated_saving_eur") is not None else "") + ")")
        if ph:
            parts.append("pre-heat in a cheap hour before the forecast cold spell "
                         f"(first cold hour {ph.get('first_cold_hour_utc')})")
        risk = bool(f.get("comfort_at_risk"))
        return _out("energy_optimization", "weather_load" if ph else "unknown", "low", 0.8 if bp else 0.7,
                    f"{asset}: day-ahead tariff allows load shifting: " + " and ".join(parts)
                    + (f". Comfort is at risk in {f.get('rooms_cold_now', 0)} room(s), so it must be deferred"
                       if risk else "") + ".",
                    ["price_spread_eur_kwh", "battery_plan", "preheat_plan", "simulated_saving_eur",
                     "comfort_at_risk"])

    if det == "occupancy_surge":
        gap = int(f.get("gap_rooms") or 0)
        residual = int(f.get("residual_gap_after_oncall") or 0)
        day = f"{f.get('target_date_local', '?')} ({f.get('weekday', '?')})"
        load = (f"{f.get('occupancy_rooms')} rooms occupied ({f.get('occupancy_pct')}%), "
                f"{f.get('arrivals')} arrivals, {f.get('departures')} departures, "
                f"{f.get('same_day_turnovers')} same-day turnovers")
        staff = (f"{f.get('cleaners_scheduled')} cleaner(s) known on shift"
                 + (f" ({', '.join(f['cleaners_absent_known'])} absent)" if f.get("cleaners_absent_known") else "")
                 + f", capacity {f.get('capacity_rooms_before_arrivals')} rooms before the 15:00 arrivals")
        used = ["gap_rooms", "occupancy_rooms", "arrivals", "departures", "same_day_turnovers",
                "cleaners_scheduled", "cleaners_absent_known", "capacity_rooms_before_arrivals",
                "robots_in_service", "oncall_would_cover_rooms", "residual_gap_after_oncall"]
        if f.get("contingent") or gap <= 0:
            return _out("occupancy_surge", "staffing", "medium", 0.65,
                        f"{day}: surge expected ({load}). Known staff ({staff}) still covers it, but with no "
                        f"slack: if one more cleaner drops out, "
                        f"{f.get('stress_gap_rooms_if_one_cleaner_out')} room(s) could not be cleaned in time.",
                        used + ["stress_gap_rooms_if_one_cleaner_out"])
        tail = (f" The on-call housekeeper could cover {f.get('oncall_would_cover_rooms')} of them for waiting "
                f"guests (needs approval, ready about {f.get('oncall_ready_window_local')} local)"
                + (f"; {residual} room(s) stay unmet even then." if residual else "."))
        return _out("occupancy_surge", "staffing", "critical" if residual else "high", 0.8,
                    f"{day}: demand exceeds known cleaning capacity ({load}). {staff}. "
                    f"Unmet gap: {gap} room(s) (~{f.get('gap_minutes')} cleaning min), "
                    f"{f.get('arrivals_at_risk')} arrival(s) at risk." + tail,
                    used)

    if det == "ventilation_maintenance":
        return _ventilation_maintenance(asset, f)

    if det == "ventilation_sensor_gap":
        hours = f.get("gap_hours_so_far") or f.get("duration_h") or 0
        corr = bool(f.get("fan_airflow_drift_corroborates"))
        pre = f.get("pre_gap_dp_vs_baseline_ratio")
        reason = (f"{asset}: filter_dp_pa has been blank for {hours} h (last valid {f.get('last_valid_utc')}). "
                  "A gap is missing data, not a pressure drop, so it is treated as a sensor/data fault, not as "
                  "filter degradation.")
        if pre:
            reason += f" Before the gap the pressure was {pre:.2f}x its clean baseline (context only)."
        if corr:
            reason += (f" Fan power per airflow is {f.get('fan_power_vs_baseline_ratio')}x baseline, which is "
                       "consistent with a loading filter but only corroborates; restore the sensor to confirm.")
        else:
            reason += " Restore the sensor feed so the filter can be judged again."
        return _out("data_quality", "sensor_fault", "medium" if hours >= 24 else "low", 0.8, reason,
                    ["gap_hours_so_far", "last_valid_utc", "pre_gap_dp_vs_baseline_ratio",
                     "fan_power_vs_baseline_ratio", "fan_airflow_drift_corroborates"])

    if det == "logistics":
        return _logistics(asset, f)

    return _out("data_quality", "unknown", "low", 0.2,
                f"{asset}: no rule for detector '{det}', flagged for review.", [])


def _ventilation_maintenance(asset: str, f: Dict[str, Any]) -> Dict[str, Any]:
    ratio, sf = f.get("dp_vs_baseline_ratio"), f.get("airflow_shortfall_share") or 0.0
    fan_ratio = f.get("fan_power_vs_baseline_ratio") or 0.0
    blind = bool(f.get("dp_sensor_missing"))
    used = ["dp_vs_baseline_ratio", "dp_rise_share_per_day", "fan_power_vs_baseline_ratio",
            "airflow_shortfall_share", "technician_next_slot_utc", "contractor_needed", "technician_unavailable"]
    if blind:
        sev = "high" if sf >= 0.10 else "medium"
        reason = (f"{asset}: the filter pressure sensor is blank, but measured airflow is {sf:.0%} below its usual "
                  f"schedule while fan power is {fan_ratio:.2f}x baseline, which points to a restricted flow "
                  "(likely a loaded filter; not confirmed by pressure).")
        conf = 0.6
        used = ["airflow_shortfall_share", "fan_power_vs_baseline_ratio", "dp_sensor_missing"] + used[4:]
    else:
        ratio = ratio or 0.0
        sev = "high" if (ratio >= 1.8 or sf >= 0.05) else ("medium" if ratio >= 1.35 else "low")
        rise = f.get("dp_rise_share_per_day")
        reason = (f"{asset}: filter pressure drop is {ratio:.2f}x its clean baseline"
                  + (f" and rising {rise:.0%}/day" if rise else "")
                  + f" (fan power {fan_ratio:.2f}x baseline"
                  + (f", airflow {sf:.0%} under schedule" if sf >= 0.02 else "") + "), so the filter is loading.")
        conf = 0.85 if (ratio >= 1.35 and (fan_ratio >= 1.1 or sf >= 0.02)) else (0.75 if ratio >= 1.35 else 0.7)
    out_of_office = sorted({str(u.get("status")) for u in (f.get("technician_unavailable") or [])})
    if f.get("contractor_needed"):
        reason += (" No in-house technician slot is available soon"
                   + (f" ({'/'.join(out_of_office)} on the roster)" if out_of_office else "")
                   + "; an external HVAC contractor should be requested.")
    elif f.get("technician_next_slot_utc"):
        reason += (f" Next real technician slot: {f['technician_id']} from {f['technician_next_slot_utc']} "
                   f"(shift {f.get('technician_shift_local')}).")
    if f.get("open_work_order"):
        reason += f" Work order {f['open_work_order']} is already open for AHU1."
    return _out("ventilation_maintenance", "equipment_fault", sev, conf, reason, used + ["open_work_order"])


def _logistics(asset: str, f: Dict[str, Any]) -> Dict[str, Any]:
    subject = f.get("subject")
    helps = f.get("extra_service_helps", True)
    nxt = f.get("next_regular_service_utc") or f.get("next_housekeeping_emptying_utc")
    hrs = f.get("hours_to_next_service")
    capped = bool(f.get("capped_at_100"))
    if subject == "floor_bin":
        sev = "high" if capped else "medium"
        reason = (f"{asset}: floor bin is {f.get('fill_pct')}% full"
                  + (" (capped at 100%: it may already be overflowing)" if capped else
                     f" and projected {f.get('projected_pct_at_next_service')}% by the next emptying at {nxt}")
                  + ("; a robot can carry it to the container." if f.get("robot_can_carry") else
                     "; the central container has no room, so housekeeping should empty it."))
        return _out("logistics", "cleaning_activity", sev, 0.65 if capped else 0.75, reason,
                    ["fill_pct", "capped_at_100", "projected_pct_at_next_service", "robot_can_carry"])
    if subject == "vending":
        prod = f.get("limiting_product")
        hl = f.get("hours_to_reserve") or 0
        sev = "high" if (f.get("stock_units") == 0 or not helps or hl < f.get("lead_time_h", 24)) else "medium"
        reason = (f"{asset}: {prod} stock is {f.get('stock_units'):g} units, burning "
                  f"{f.get('burn_units_per_h')} units/h; projected {f.get('projected_units_at_next_service')} units "
                  f"at the next regular restock ({nxt}, in {hrs} h).")
        reason += (f" An extra restock (~{f.get('lead_time_h', 24):g} h lead time) arrives from "
                   f"{f.get('earliest_extra_service_utc')}, so it should be ordered now." if helps else
                   " An extra restock would arrive after the regular one, so it cannot help.")
        return _out("logistics", "guest_usage", sev, 0.75, reason,
                    ["stock_units", "burn_units_per_h", "projected_units_at_next_service", "hours_to_next_service",
                     "extra_service_helps", "earliest_extra_service_utc"])
    hf = f.get("hours_to_full") or 0
    sev = "high" if (capped or not helps or hf < f.get("lead_time_h", 24)) else "medium"
    reason = (f"{asset}: container is {f.get('fill_pct')}% full and filling {f.get('fill_rate_pct_per_h')} %/h; it is "
              f"projected at {f.get('projected_pct_at_next_service')}% by the next regular pickup ({nxt}, in {hrs} h), "
              f"i.e. full in about {hf} h.")
    reason += (f" An extra pickup (~{f.get('lead_time_h', 24):g} h lead time) arrives from "
               f"{f.get('earliest_extra_service_utc')}"
               + (" - already later than the projected full time, so order it immediately." if hf < f.get("lead_time_h", 24)
                  else ", before it is full if ordered now.") if helps else
               " An extra pickup would arrive after the regular one, so it cannot help; a person must arrange relief.")
    return _out("logistics", "guest_usage", sev, 0.75, reason,
                ["fill_pct", "fill_rate_pct_per_h", "projected_pct_at_next_service", "hours_to_full",
                 "hours_to_next_service", "extra_service_helps", "earliest_extra_service_utc"])
