"""Compose Stage-B per-episode operators without dataset-specific branches."""

from __future__ import annotations

import numpy as np

from robot_data_processing.types import EpisodeResult

from .decision import FinalDecisionOperator
from .input_adapter import InputOperator
from .prefix import PrefixOperator
from .stage1 import Stage1Operator
from .stage2 import Stage2Operator
from .stage3 import Stage3Operator
from .stage4 import Stage4Operator


class QualityOperator:
    """Run Stage1–4 and the shared prefix/final policy for one pseudo-v3 episode."""

    def run(self, episode_index: int, cfg, statistics) -> EpisodeResult:
        episode = InputOperator().run(episode_index, cfg)
        if episode is None:
            return EpisodeResult(
                episode_index=episode_index,
                num_frames=0,
                discard=True,
                discard_reasons=["missing_parquet"],
                step_validity_mask=np.array([], dtype=np.int8),
            )
        stage1 = Stage1Operator().run(episode, cfg, statistics)
        stage2 = Stage2Operator().run(episode, cfg, statistics)
        stage3 = Stage3Operator().run(episode, cfg, statistics)
        prefix = PrefixOperator().run(episode.num_frames, stage1, stage2, stage3, cfg)
        stage4 = Stage4Operator().run(episode, prefix.valid_end, cfg)
        return FinalDecisionOperator().run(
            episode, cfg, statistics, stage1, stage2, stage3, prefix, stage4
        )
