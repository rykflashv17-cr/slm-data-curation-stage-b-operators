"""Dataset-neutral contracts between Stage-B quality operators.

The quality arrays are derived from Stage-A pseudo-v3 Parquet. Their width is
specified by each dataset's quality YAML, while frame count and identity are
kept stable through Stage1–4 and the final decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class QualityEpisode:
    """One aligned episode represented in the configured quality space."""

    episode_index: int
    path: Path
    state: np.ndarray
    action: np.ndarray
    startup_exclude: np.ndarray
    stage1_exclude: np.ndarray

    @property
    def num_frames(self) -> int:
        return int(self.state.shape[0])

    def __post_init__(self) -> None:
        if self.state.ndim != 2 or self.action.ndim != 2:
            raise ValueError("Stage-B quality state/action must be 2D arrays")
        if self.state.shape[0] != self.action.shape[0]:
            raise ValueError("Stage-B quality state/action frame counts differ")


@dataclass(frozen=True)
class StatisticsBundle:
    global_stats: Any
    stage1_stats: Any
    alignment_stats: Any
    alignment_lag: int
    global_cache: Path
    stage1_cache: Path
    alignment_cache: Path


@dataclass(frozen=True)
class PrefixDecision:
    abnormal_frames: np.ndarray
    keep_mask: np.ndarray
    valid_end: int
    trigger: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class StaticDecision:
    result: Any
    remove_frames: np.ndarray
