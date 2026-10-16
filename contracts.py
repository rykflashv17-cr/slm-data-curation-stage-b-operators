"""Small, explicit contracts between the Stage B operators."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class QualityEpisode:
    episode_index: int
    path: Path
    state: np.ndarray
    action: np.ndarray
    startup_exclude: np.ndarray
    stage1_exclude: np.ndarray

    @property
    def num_frames(self) -> int:
        return int(self.state.shape[0])


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
