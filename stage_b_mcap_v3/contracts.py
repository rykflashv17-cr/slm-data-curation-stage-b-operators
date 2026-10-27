"""Small, explicit contracts for the MCAP-in/MCAP-out Stage B pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class McapEpisodeRef:
    episode_index: int
    dataset_root: Path
    mcap_path: Path
    rel_path: str
    part: str = ""
    task: str = ""


@dataclass(frozen=True)
class QualityEpisode:
    ref: McapEpisodeRef
    state: np.ndarray
    action: np.ndarray
    frame_times_ns: np.ndarray
    startup_exclude: np.ndarray
    stage1_exclude: np.ndarray

    def __post_init__(self) -> None:
        n = self.state.shape[0]
        if self.state.ndim != 2 or self.action.ndim != 2 or self.action.shape[0] != n:
            raise ValueError("state/action must be aligned two-dimensional arrays")
        if self.frame_times_ns.shape != (n,) or self.frame_times_ns.dtype.kind not in "iu":
            raise ValueError("frame_times_ns must contain one integer timestamp per frame")
        if n and np.any(np.diff(self.frame_times_ns) < 0):
            raise ValueError("reference frame timestamps must not go backward")

    @property
    def episode_index(self) -> int:
        return self.ref.episode_index

    @property
    def num_frames(self) -> int:
        return self.state.shape[0]


@dataclass(frozen=True)
class StatisticsBundle:
    global_stats: Any
    stage1_stats: Any
    alignment_stats: Any
    alignment_lag: int
    cache_dir: Path


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


@dataclass(frozen=True)
class GripperGapDecision:
    valid_mask: np.ndarray
    invalid_by_side: dict[str, np.ndarray]
    invalid_ranges: tuple[dict[str, Any], ...]
    max_nearest_gap_ns: int | None

    def __post_init__(self) -> None:
        if self.valid_mask.ndim != 1 or self.valid_mask.dtype != np.bool_:
            raise ValueError("gripper valid_mask must be one-dimensional boolean")
        if any(mask.shape != self.valid_mask.shape for mask in self.invalid_by_side.values()):
            raise ValueError("gripper side masks must match the quality timeline")


@dataclass(frozen=True)
class CleaningDecision:
    ref: McapEpisodeRef
    frame_times_ns: np.ndarray
    keep_mask: np.ndarray
    discard_episode: bool
    reasons: tuple[str, ...]
    metrics: dict[str, Any]
    gripper_gap_ranges: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.keep_mask.shape != self.frame_times_ns.shape:
            raise ValueError("keep_mask length must equal frame_times_ns length")
