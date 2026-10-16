"""5. Per-episode Stage3 extreme-value detector."""

from robot_data_processing.stages.stage3_extreme_value import Stage3Config, run_stage3


class Stage3Operator:
    def run(self, episode, cfg, statistics):
        return run_stage3(
            episode.state, episode.action, statistics.global_stats,
            cfg.stage3 or Stage3Config(), schema=cfg.schema,
            startup_exclude_per_joint=episode.startup_exclude,
        )
