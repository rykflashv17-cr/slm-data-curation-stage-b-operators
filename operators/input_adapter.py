"""1. Stage-A parquet/pseudo-v3 episode -> canonical quality episode."""

from __future__ import annotations

from robot_data_processing.adapter_hooks import has_adapter_quality_loader
from robot_data_processing.loader import episode_parquet_path, load_quality_arrays
from robot_data_processing.mask import (
    compute_per_joint_action_zero_exclude,
    compute_per_joint_stage1_exclude,
)
from robot_data_processing.preprocess import expand_compact_exclude_to_action
from robot_data_processing.transforms import robomind_ur_compact_teleop

from ..contracts import QualityEpisode


class InputOperator:
    def run(self, episode_index: int, cfg) -> QualityEpisode | None:
        path = episode_parquet_path(cfg.dataset_root, episode_index)
        if not path.exists() and not has_adapter_quality_loader():
            return None
        arrays = load_quality_arrays(cfg.dataset_root, episode_index, schema=cfg.schema)
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
