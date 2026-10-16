"""4. Per-episode Stage2 state/action direction agreement."""

from robot_data_processing.stages.stage2_trend_alignment import (
    Stage2Config,
    canonical_to_stage2_fields,
    run_stage2_on_fields,
)


class Stage2Operator:
    def run(self, episode, cfg, statistics):
        # The upstream fill may mutate this newly built dict; the input episode is untouched.
        fields = canonical_to_stage2_fields(
            episode.state, episode.action, embodiment=cfg.schema.embodiment
        )
        return run_stage2_on_fields(
            fields, cfg.stage2 or Stage2Config(), fill_lag=statistics.alignment_lag
        )
