"""3. Per-episode Stage1 sudden-change detector."""

from robot_data_processing.stages.stage1_sudden_change import Stage1Config, run_stage1


class Stage1Operator:
    def run(self, episode, cfg, statistics):
        return run_stage1(
            episode.state, episode.action, cfg.stage1 or Stage1Config(),
            schema=cfg.schema, startup_exclude_per_joint=episode.stage1_exclude,
            global_stats=statistics.stage1_stats,
        )
