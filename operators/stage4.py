"""7. Stage4 static-run shortening inside the valid prefix."""

from __future__ import annotations

import numpy as np

from robot_data_processing.stages.stage4_static_interval import Stage4Config, run_stage4

from ..contracts import StaticDecision


class Stage4Operator:
    def run(self, episode, valid_end: int, cfg) -> StaticDecision:
        result = run_stage4(
            episode.state[:valid_end], episode.action[:valid_end],
            cfg.stage4 or Stage4Config(),
        )
        remove = np.zeros(episode.num_frames, dtype=bool)
        remove[:valid_end] = result.remove_frames
        return StaticDecision(result, remove)
