"""6. Compose Stage1/2/3 findings into the kept prefix."""

from __future__ import annotations

import numpy as np

from robot_data_processing.mask import build_frame_keep_mask

from ..contracts import PrefixDecision


class PrefixOperator:
    def run(self, num_frames: int, stage1, stage2, stage3, cfg) -> PrefixDecision:
        abnormal = stage1.abnormal_frames.copy()
        reasons = list(stage1.discard_reasons)
        if stage2.discard:
            abnormal[:] = True
            reasons.extend(f"stage2:{reason}" for reason in stage2.discard_reasons)
        else:
            abnormal |= stage3.remove_frames
            reasons.extend(f"stage3:{reason}" for reason in stage3.discard_reasons)

        margin = int(cfg.prefix_truncate_margin_frames)
        if margin > 0 and abnormal.any():
            first_bad = int(np.flatnonzero(abnormal)[0])
            abnormal[max(1, first_bad - margin):first_bad] = True

        keep = build_frame_keep_mask(num_frames, abnormal)
        valid_end = int(keep.sum()) if keep.any() else 0
        trigger = None
        if not stage2.discard and valid_end < num_frames:
            firsts = {
                name: int(np.flatnonzero(flags)[0])
                for name, flags in (("stage1", stage1.abnormal_frames), ("stage3", stage3.remove_frames))
                if flags.any()
            }
            trigger = min(firsts, key=firsts.get) if firsts else None
            if trigger == "stage1" and stage1.rotation_joint_first_frame == firsts["stage1"]:
                trigger = "stage1_rotation_joint"
        return PrefixDecision(abnormal, keep, valid_end, trigger, tuple(reasons))
