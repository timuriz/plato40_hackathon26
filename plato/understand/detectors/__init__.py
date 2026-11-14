"""Causal detectors. Each takes an Observation and returns a time-sorted list of Candidates."""
from .comfort import detect_comfort
from .energy import detect_energy
from .logistics import detect_logistics
from .maintenance import detect_maintenance
from .quality import detect_quality
from .turnaround import detect_turnaround
from .water import detect_water
from .weekend import detect_weekend

__all__ = ["detect_water", "detect_turnaround", "detect_comfort", "detect_quality", "detect_energy",
           "detect_maintenance", "detect_logistics", "detect_weekend", "ALL_DETECTORS"]

ALL_DETECTORS = (detect_water, detect_turnaround, detect_comfort, detect_quality, detect_energy,
                 detect_maintenance, detect_logistics, detect_weekend)
