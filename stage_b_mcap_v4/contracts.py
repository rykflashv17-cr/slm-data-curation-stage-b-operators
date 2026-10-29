"""Typed data passed between the three v4 operators."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SourceIdentity:
    st_dev: int
    st_ino: int
    st_size: int
    st_mtime_ns: int

    @classmethod
    def capture(cls, path: Path) -> "SourceIdentity":
        value = Path(path).lstat()
        return cls(*(int(getattr(value, name)) for name in cls.__dataclass_fields__))


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
    source_identity: SourceIdentity | None = None

    def __post_init__(self) -> None:
        state, action = np.asarray(self.state, dtype=np.float64), np.asarray(self.action, dtype=np.float64)
        if state.ndim != 2 or action.ndim != 2 or state.shape[0] != action.shape[0]:
            raise ValueError("state/action must be aligned two-dimensional arrays")
        times = np.asarray(self.frame_times_ns)
        if times.shape != (len(state),) or times.dtype.kind not in "iu":
            raise ValueError("one integer source timestamp is required per quality frame")
        if len(times) and np.any(np.diff(times) < 0):
            raise ValueError("source timestamps must not go backward")
        for name in ("startup_exclude", "stage1_exclude"):
            mask = np.asarray(getattr(self, name), dtype=bool)
            if mask.shape != action.shape:
                raise ValueError(f"{name} must match the action array")
            object.__setattr__(self, name, mask)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "frame_times_ns", times.astype(np.int64, copy=False))

    @property
    def episode_index(self) -> int:
        return self.ref.episode_index

    @property
    def num_frames(self) -> int:
        return len(self.state)


@dataclass(frozen=True)
class StatisticsBundle:
    global_stats: Any
    stage1_stats: Any
    alignment_stats: Any
    alignment_lag: int
    cache_dir: Path


def frame_ranges(mask: np.ndarray, times: np.ndarray) -> list[dict[str, int]]:
    """Diagnostic frame ranges; a rejected file is always removed in full."""
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != times.shape or mask.ndim != 1:
        raise ValueError("range mask must match the source timeline")
    if not len(mask):
        return []
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    terminal_dt = max(0, int(times[-1] - times[-2])) if len(times) > 1 else 0
    return [{
        "start_frame": int(start), "end_frame_exclusive": int(end),
        "start_time_ns": int(times[start]),
        "end_time_ns": int(times[end]) if end < len(times) else int(times[-1]) + terminal_dt,
    } for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]


def _plain(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.generic):
        return _plain(value.item())
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


@dataclass(frozen=True)
class QualityDecision:
    ref: McapEpisodeRef
    frame_times_ns: np.ndarray
    bad_mask: np.ndarray
    reject_file: bool
    reasons: tuple[str, ...]
    stage_results: dict[str, dict[str, Any]]
    source_identity: SourceIdentity | None = None

    def __post_init__(self) -> None:
        mask = np.asarray(self.bad_mask, dtype=bool)
        if mask.ndim != 1 or mask.shape != self.frame_times_ns.shape:
            raise ValueError("bad_mask must match the source timeline")
        if mask.any() and not self.reject_file:
            raise ValueError("v4 must reject the entire MCAP when any frame is bad")
        object.__setattr__(self, "bad_mask", mask)

    @property
    def num_frames(self) -> int:
        return len(self.frame_times_ns)

    def to_record(self) -> dict[str, Any]:
        detected = []
        for name, result in self.stage_results.items():
            for interval in result.get("detected_ranges", []):
                detected.append({"stage": name, **interval})
        return _plain({
            "episode_index": self.ref.episode_index,
            "source": str(self.ref.mcap_path),
            "status": "rejected" if self.reject_file else "kept",
            "reject_file": bool(self.reject_file),
            "reasons": list(self.reasons),
            "num_frames": self.num_frames,
            "detected_bad_frames": int(np.count_nonzero(self.bad_mask)),
            "detected_ranges": detected,
            "effective_removed_ranges": frame_ranges(
                np.ones(self.num_frames, dtype=bool), self.frame_times_ns,
            ) if self.reject_file else [],
            "stage_results": self.stage_results,
            "source_identity": asdict(self.source_identity) if self.source_identity else None,
        })
