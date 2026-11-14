"""UNDERSTAND stage, step 1: causal detectors + episode grouping.

    cands = detect_all(obs)              # every candidate from every detector, time-sorted
    episodes = build_episodes(cands)     # merged into a few Emissions per incident

Step 2 (turning an Emission's evidence packet into issue_type/cause/severity) lives in ``plato.llm``.
"""
from __future__ import annotations

from typing import List

from .candidate import Candidate
from .detectors import ALL_DETECTORS
from .episodes import Emission, build_episodes

__all__ = ["Candidate", "Emission", "detect_all", "build_episodes"]


def detect_all(obs) -> List[Candidate]:
    out: List[Candidate] = []
    for det in ALL_DETECTORS:
        out.extend(det(obs))
    out.sort(key=lambda c: (c.t, c.detector, c.asset))
    return out
