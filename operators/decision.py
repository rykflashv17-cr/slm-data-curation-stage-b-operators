"""8. Final Stage B verdict, frame mask, audit and optional Stage5 metadata."""

from __future__ import annotations

import numpy as np

from robot_data_processing.mask import build_frame_keep_mask
from robot_data_processing.pipeline import _source_clock, tail_repeat_start
from robot_data_processing.stages.stage1_sudden_change import EEF_SPIKE, GRIPPER_QUICK_REVERSAL
from robot_data_processing.stages.stage5_frame_alignment import (
    Stage5Config,
    read_stage5_raw_context,
    run_stage5,
)
from robot_data_processing.stages.state_action_temporal_alignment import (
    StateActionAlignConfig,
    alignment_metadata,
)
from robot_data_processing.types import EpisodeResult


class FinalDecisionOperator:
    def run(self, episode, cfg, statistics, stage1, stage2, stage3, prefix, stage4) -> EpisodeResult:
        num_frames = episode.num_frames
        tail_start = num_frames
        if cfg.trim_tail_repeat_frames and num_frames > 1:
            timestamps = _source_clock(episode.path, episode.episode_index, num_frames)
            if timestamps is not None:
                tail_start = tail_repeat_start(timestamps)
        tail_remove = np.zeros(num_frames, dtype=bool)
        tail_remove[tail_start:] = True

        mask = build_frame_keep_mask(
            num_frames, prefix.abnormal_frames, stage4.remove_frames | tail_remove
        )
        kept_frames = int(mask.sum())
        first_removed = int(np.flatnonzero(mask == 0)[0]) if kept_frames < num_frames else None
        reasons = list(prefix.reasons)
        discard = bool(stage2.discard) or bool(stage3.discard)
        if any(r.startswith((GRIPPER_QUICK_REVERSAL, EEF_SPIKE)) for r in stage1.discard_reasons):
            discard = True
        if cfg.discard_short_prefix and kept_frames < cfg.min_episode_length:
            discard = True
            reasons.append(f"kept_frames={kept_frames}<{cfg.min_episode_length}")
        if first_removed is not None:
            reasons.append(f"first_removed_frame={first_removed}")

        stage5_cfg = cfg.stage5 or Stage5Config()
        stage5_meta = {"stage5_applied": False}
        if stage5_cfg.enabled:
            raw_context = read_stage5_raw_context(episode.path, cfg.schema, stage5_cfg)
            result5 = run_stage5(
                cfg.dataset_root, episode.episode_index, cfg.schema, raw_context, stage5_cfg
            )
            stage5_meta = {
                "stage5_applied": result5.applied,
                "stage5_reference_frame": stage5_cfg.reference_frame,
                "stage5_reference_forward": result5.reference_forward_xyz.tolist(),
                "stage5_rotation_correction_euler_xyz": list(stage5_cfg.rotation_correction_euler_xyz),
                "aligned_state_shape": list(result5.aligned_state.shape),
            }

        align_cfg = cfg.state_action_alignment or StateActionAlignConfig()
        align_meta = alignment_metadata(
            align_cfg, statistics.alignment_stats, statistics.alignment_lag
        )
        return EpisodeResult(
            episode_index=episode.episode_index,
            num_frames=num_frames,
            discard=discard,
            discard_reasons=reasons,
            step_validity_mask=mask,
            first_abnormal_frame=first_removed,
            stage1_flagged_frames=stage1.flagged_count,
            stage2_da_mean=stage2.da_mean,
            stage2_da_per_dim=stage2.da_per_dim,
            stage2_lags=stage2.lags,
            stage3_excluded_frames=stage3.excluded_count,
            stage4_removed_frames=stage4.result.removed_count,
            metadata={
                "stage1_hard_limit": stage1.hard_limit_hit,
                "stage1_gripper_faults": stage1.gripper_faults,
                "stage1_rotation_joint_steps": stage1.rotation_joint_steps,
                "valid_prefix": kept_frames,
                "prefix_valid_end": prefix.valid_end,
                "prefix_truncate_trigger": prefix.trigger,
                "tail_repeat_start": tail_start if tail_start < num_frames else None,
                "kept_frames": kept_frames,
                "low_valid_prefix": kept_frames < cfg.min_episode_length,
                "stage4_static_runs_shortened": stage4.result.static_runs_shortened,
                "stage4_static_frames_removed": stage4.result.static_frames_removed,
                "startup_exclude_per_joint_frames": int(episode.startup_exclude.sum()),
                "stage1_exclude_frames": int(episode.stage1_exclude.sum()),
                "stage1_post_zero_grace_frames": cfg.stage1_post_zero_grace_frames,
                "embodiment": cfg.schema.embodiment,
                "state_dim": cfg.schema.pipeline_state_dim,
                "action_dim": cfg.schema.pipeline_action_dim,
                "stage2_dim_names": stage2.dim_names,
                "stage2_filled_action_eef": stage2.filled_action_eef,
                "stage2_fill_lags": stage2.fill_lags,
                "stage2_episode_lag": stage2.episode_lag,
                "stage2_lag_mean": stage2.lag_mean,
                "stage2_lag_mode": stage2.lag_mode,
                "stage2_lag_source": stage2.lag_source,
                **stage5_meta,
                **align_meta,
            },
        )
