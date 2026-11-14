"""Causal detectors. Each takes an Observation and returns a time-sorted list of Candidates."""
from .comfort import detect_comfort
from .quality import detect_quality
from .turnaround import detect_turnaround
from .water import detect_water

__all__ = ["detect_water", "detect_turnaround", "detect_comfort", "detect_quality", "ALL_DETECTORS"]

ALL_DETECTORS = (detect_water, detect_turnaround, detect_comfort, detect_quality)
