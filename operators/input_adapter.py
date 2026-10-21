"""Read Stage-A pseudo-v3 Parquet in each dataset's Stage-B quality space.

Source adapters can override quality I/O for raw H5/MCAP. Stage B must read
the aligned Parquet instead. Most Stage-B schemas use the engine's standard
field packing; a 14-dimensional H5 schema needs the dataset's own packing.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np

from robot_data_processing import adapter_hooks
from robot_data_processing.loader import (
    episode_parquet_path,
    load_quality_arrays,
)
from robot_data_processing.mask import (
    compute_per_joint_action_zero_exclude,
    compute_per_joint_stage1_exclude,
)
from robot_data_processing.preprocess import expand_compact_exclude_to_action
from robot_data_processing.transforms import robomind_ur_compact_teleop
from robot_data_processing.normalize.standard_types import EpisodeRef, StandardEpisode
from robot_data_processing.v3_layout import read_standard_fields

from ..contracts import QualityEpisode


@contextmanager
def _without_adapter_hook() -> Iterator[None]:
    """Use the shared loader's standard-Parquet branch without raw hooks."""
    old_adapter = adapter_hooks.get_adapter()
    old_package = getattr(adapter_hooks, "_PACKAGE_DIR", None)
    if old_adapter is not None:
        adapter_hooks.clear_adapter_hooks()
    try:
        yield
    finally:
        if old_adapter is not None:
            adapter_hooks._ADAPTER = old_adapter
            adapter_hooks._PACKAGE_DIR = old_package


def _native_pseudo_arrays(root: Path, episode_index: int, schema) -> dict:
    with _without_adapter_hook():
        return load_quality_arrays(
            root, episode_index, schema=schema, input_format="pseudo_v3"
        )


class StageBQualityAdapter:
    """Proxy a dataset adapter while forcing quality I/O through Stage-A Parquet."""

    def __init__(self, source_adapter) -> None:
        self.source_adapter = source_adapter

    def __getattr__(self, name: str):
        return getattr(self.source_adapter, name)

    def load_quality_arrays(
        self, root: Path, episode_index: int, schema=None, **kwargs,
    ) -> dict[str, np.ndarray]:
        del kwargs
        if schema is None:
            raise ValueError("Stage-B quality loading requires the configured schema")
        root = Path(root)
        expected = (int(schema.pipeline_state_dim), int(schema.pipeline_action_dim))
        native = None
        # The shared standard-field packer returns 32d for robot schemas and
        # 14d for Ego schemas. Skip that read for the H5 adapters' 14d robot
        # quality space; their own mapper reads the same Stage-A Parquet once.
        ego_schema = schema.embodiment in ("egodex", "egostandard", "egoverse")
        if ego_schema or expected == (32, 32):
            native = _native_pseudo_arrays(root, episode_index, schema)
            state = np.asarray(native["state"], dtype=np.float64)
            action = np.asarray(native["action"], dtype=np.float64)
            if state.ndim == 2 and action.ndim == 2 and (
                state.shape[1], action.shape[1]
            ) == expected:
                return native

        # RoboMIND H5 Stage B stores standard arm/gripper fields but keeps a
        # 14d quality schema. The generic standard packer produces 32d, so let
        # its dataset adapter select the actual 14 dimensions from Parquet.
        if not hasattr(self.source_adapter, "to_quality_arrays"):
            raise ValueError(
                f"Stage-B quality schema {expected} needs a dataset quality mapper"
            )
        path = episode_parquet_path(root, episode_index)
        raw = read_standard_fields(path)
        ref = EpisodeRef(episode_index=episode_index, dataset_root=root)
        meta = {
            "dataset_root": str(root),
            "source_episode_index": episode_index,
            "input_format": "pseudo_v3",
            "identity_p1": True,
        }
        if hasattr(self.source_adapter, "to_standard_episode_from_aligned"):
            standard = self.source_adapter.to_standard_episode_from_aligned(ref, raw, meta)
        else:
            num_frames = int(next(iter(raw.values())).shape[0]) if raw else 0
            standard = StandardEpisode(
                episode_index=episode_index,
                num_frames=num_frames,
                fields=raw,
                meta=meta,
            )
        state, action = self.source_adapter.to_quality_arrays(standard)
        state = np.asarray(state, dtype=np.float64)
        action = np.asarray(action, dtype=np.float64)
        if state.ndim != 2 or action.ndim != 2 or (
            state.shape[1], action.shape[1]
        ) != expected or state.shape[0] != action.shape[0]:
            raise ValueError(
                f"Stage-B adapter quality shape {(state.shape, action.shape)} "
                f"does not match dimensions {expected}"
            )
        return {"state": state, "action": action}

    def resolve_source_video(self, root: Path, episode_index: int, key: str):
        # The pseudo-v3 root already contains videos; source-format resolvers
        # may otherwise point at a raw H5 or MCAP video cache.
        return None


def install_stage_b_quality_adapter() -> None:
    """Replace a raw-source adapter hook with the Stage-B proxy in this process."""
    adapter = adapter_hooks.get_adapter()
    if adapter is not None and not isinstance(adapter, StageBQualityAdapter):
        adapter_hooks._ADAPTER = StageBQualityAdapter(adapter)


@contextmanager
def stage_b_adapter_io() -> Iterator[None]:
    """Scope the Stage-B proxy and restore upstream's source adapter afterward."""
    old_adapter = adapter_hooks.get_adapter()
    install_stage_b_quality_adapter()
    try:
        yield
    finally:
        adapter_hooks._ADAPTER = old_adapter


class InputOperator:
    def run(self, episode_index: int, cfg) -> QualityEpisode | None:
        path = episode_parquet_path(cfg.dataset_root, episode_index)
        if not path.exists():
            return None
        with stage_b_adapter_io():
            arrays = load_quality_arrays(
                cfg.dataset_root, episode_index, schema=cfg.schema,
                input_format="pseudo_v3",
            )
        state, action = arrays["state"], arrays["action"]
        action_for_masks = action
        if cfg.schema.embodiment == "robomind_ur":
            _, action_for_masks = robomind_ur_compact_teleop(state, action)
        startup = compute_per_joint_action_zero_exclude(action_for_masks, cfg.action_zero_epsilon)
        stage1 = compute_per_joint_stage1_exclude(
            action_for_masks, cfg.action_zero_epsilon, cfg.stage1_post_zero_grace_frames
        )
        if cfg.schema.embodiment == "robomind_ur":
            startup = expand_compact_exclude_to_action(startup, action.shape[1])
            stage1 = expand_compact_exclude_to_action(stage1, action.shape[1])
        return QualityEpisode(episode_index, path, state, action, startup, stage1)
