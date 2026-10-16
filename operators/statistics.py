"""2. Dataset-level Stage1, Stage3 and optional P4 lag statistics."""

from __future__ import annotations

from robot_data_processing.stage1_stats import load_or_compute_stage1_stats
from robot_data_processing.state_action_lag_stats import load_or_compute_action_state_lag
from robot_data_processing.stages.stage1_sudden_change import Stage1Config
from robot_data_processing.stages.state_action_temporal_alignment import (
    StateActionAlignConfig,
    resolve_alignment_lag,
)
from robot_data_processing.stats import load_or_compute_stats

from ..contracts import StatisticsBundle


class StatisticsOperator:
    def run(self, cfg, episode_indices: list[int], *, show_progress: bool = True) -> StatisticsBundle:
        global_cache = cfg.stats_cache_path or cfg.output_dir / "cache" / "global_stats.npz"
        stage1_cache = cfg.stage1_stats_cache_path or cfg.output_dir / "cache" / "stage1_global_stats.npz"
        alignment_cache = cfg.state_action_lag_cache_path or cfg.output_dir / "cache" / "state_action_lag.npz"
        global_stats = load_or_compute_stats(
            cfg.dataset_root, global_cache, episode_indices, schema=cfg.schema,
            recompute=cfg.stats_recompute, num_workers=cfg.num_workers,
            num_bins=cfg.stats_num_bins, show_progress=show_progress,
        )
        stage1_stats = load_or_compute_stage1_stats(
            cfg.dataset_root, stage1_cache, episode_indices, cfg.schema,
            cfg.stage1 or Stage1Config(), recompute=cfg.stage1_stats_recompute,
            num_workers=cfg.num_workers, num_bins=cfg.stats_num_bins,
            show_progress=show_progress,
        )
        alignment_cfg = cfg.state_action_alignment or StateActionAlignConfig()
        alignment_stats = load_or_compute_action_state_lag(
            cfg.dataset_root, alignment_cache, episode_indices, cfg.schema,
            alignment_cfg, recompute=cfg.state_action_lag_recompute,
            num_workers=cfg.num_workers, show_progress=show_progress,
        )
        lag = resolve_alignment_lag(alignment_cfg, alignment_stats)
        return StatisticsBundle(
            global_stats, stage1_stats, alignment_stats, lag,
            global_cache, stage1_cache, alignment_cache,
        )
