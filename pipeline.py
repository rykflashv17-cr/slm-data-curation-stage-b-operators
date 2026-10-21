"""Run the shared Stage-B quality policy on any Stage-A pseudo-v3 dataset."""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from pathlib import Path

from robot_data_processing.ignore_list import filter_episode_indices, load_ignore_episode_lists
from robot_data_processing.lerobot_export import export_lerobot_from_results, verify_lerobot_alignment
from robot_data_processing.stages.stage4_static_interval import Stage4Config
from robot_data_processing.stages.stage5_frame_alignment import Stage5Config
from robot_data_processing.stages.state_action_temporal_alignment import (
    StateActionAlignConfig,
    resolve_p4_plan,
    resolve_state_action_delay,
)
from robot_data_processing.types import EpisodeResult

from .contracts import StatisticsBundle
from .operators.input_adapter import install_stage_b_quality_adapter, stage_b_adapter_io
from .operators.quality import QualityOperator
from .operators.report import ReportOperator
from .operators.statistics import StatisticsOperator

_WORKER_CFG = None
_WORKER_STATS: StatisticsBundle | None = None


def process_episode(
    episode_index: int,
    cfg=None,
    statistics: StatisticsBundle | None = None,
    *,
    package_dir: Path | None = None,
) -> EpisodeResult:
    """Run Stage1–4 and both decision operators for one aligned episode."""
    cfg = cfg or _WORKER_CFG
    statistics = statistics or _WORKER_STATS
    if cfg is None or statistics is None:
        raise RuntimeError("process_episode needs PipelineConfig and StatisticsBundle")
    if package_dir is not None and not Path(package_dir).is_dir():
        raise FileNotFoundError(package_dir)
    return QualityOperator().run(episode_index, cfg, statistics)


def _init_worker(cfg, statistics: StatisticsBundle) -> None:
    global _WORKER_CFG, _WORKER_STATS
    # In a forked worker, replace any inherited raw H5/MCAP loader.
    install_stage_b_quality_adapter()
    _WORKER_CFG, _WORKER_STATS = cfg, statistics


def _worker_fn(episode_index: int) -> EpisodeResult:
    return process_episode(episode_index)


def run_pipeline(
    cfg,
    episode_indices: list[int],
    stats_episode_indices: list[int] | None = None,
    show_progress: bool = True,
    timings: dict[str, float] | None = None,
    *,
    package_dir: Path | None = None,
) -> list[EpisodeResult]:
    """Drop-in P3 replacement; statistics and quality read Stage-A Parquet.

    ``package_dir`` is optional because the upstream orchestrator supplies the
    dataset package through its adapter registry and calls P3 with the original
    signature. A direct caller may pass it for early path validation.
    """
    from tqdm import tqdm

    if package_dir is not None and not Path(package_dir).is_dir():
        raise FileNotFoundError(package_dir)
    started = time.perf_counter()
    ignore_paths = list(cfg.ignore_episodes_paths)
    if not ignore_paths and cfg.ignore_episodes_path is not None:
        ignore_paths = [cfg.ignore_episodes_path]
    ignored = load_ignore_episode_lists(ignore_paths)
    if ignored:
        original_count = len(episode_indices)
        episode_indices = filter_episode_indices(episode_indices, ignored)
        meta_dir = cfg.output_dir / "meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        with (meta_dir / "ignored_episodes.json").open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "reason": "pipeline ignore list",
                    "source": str(ignore_paths[0]) if len(ignore_paths) == 1 else None,
                    "sources": [str(p) for p in ignore_paths],
                    "ignored_count": original_count - len(episode_indices),
                    "ignore_list_size": len(ignored),
                    "episode_indices": sorted(ignored),
                },
                handle, indent=2, ensure_ascii=False,
            )
        if show_progress:
            print(f"Ignoring {len(ignored)} episodes ({original_count} -> {len(episode_indices)})")

    stats_eps = filter_episode_indices(stats_episode_indices or episode_indices, ignored)
    stats_started = time.perf_counter()
    statistics = StatisticsOperator().run(cfg, stats_eps, show_progress=show_progress)
    cfg.alignment_lag = statistics.alignment_lag
    if timings is not None:
        timings["p3_global_statistics"] = round(time.perf_counter() - stats_started, 3)

    process_started = time.perf_counter()
    results: list[EpisodeResult] = []
    if episode_indices:
        with mp.get_context("fork").Pool(
            processes=cfg.num_workers,
            initializer=_init_worker,
            initargs=(cfg, statistics),
        ) as pool:
            iterator = pool.imap_unordered(_worker_fn, episode_indices, chunksize=4)
            if show_progress:
                iterator = tqdm(iterator, total=len(episode_indices), desc="process episodes")
            results.extend(iterator)
    results.sort(key=lambda result: result.episode_index)
    if timings is not None:
        timings["p3_process_episodes"] = round(time.perf_counter() - process_started, 3)

    align_cfg = cfg.state_action_alignment or StateActionAlignConfig()
    p4_plan = resolve_p4_plan(align_cfg)
    align_lag = statistics.alignment_lag
    delay_meta = resolve_state_action_delay(align_cfg, statistics.alignment_stats, p4_plan)

    if cfg.output_mode in ("filter", "both"):
        export_started = time.perf_counter()
        with stage_b_adapter_io():
            summary = export_lerobot_from_results(
                source_root=cfg.dataset_root, output_root=cfg.output_dir,
                schema=cfg.schema, results=results,
                alignment_lag=align_lag, alignment_enabled=p4_plan.apply_shift,
                recompute_video_stats=cfg.recompute_video_stats,
                export_workers=cfg.export_workers, parallel_videos=cfg.parallel_videos,
                show_progress=show_progress, alignment_plan=p4_plan.name,
                manual_delay=p4_plan.manual_delay,
            )
        if not cfg.skip_alignment_verify:
            alignment = verify_lerobot_alignment(
                cfg.output_dir, [episode.episode_index for episode in summary.episodes]
            )
            (cfg.output_dir / "alignment_report.json").write_text(
                json.dumps(alignment, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        if timings is not None:
            timings["p3_lerobot_export"] = round(time.perf_counter() - export_started, 3)

    if cfg.output_mode in ("report", "both"):
        ReportOperator().run(
            results,
            cfg.output_dir,
            stats_meta={
                "num_frames": statistics.global_stats.num_frames,
                "num_episodes": statistics.global_stats.num_episodes,
                "cache_path": str(statistics.global_cache),
                "stage1_stats_cache_path": str(statistics.stage1_cache),
                "state_action_lag_cache_path": str(statistics.alignment_cache),
                "stage1_threshold_mode": "global",
                "embodiment": cfg.schema.embodiment,
                "state_dim": cfg.schema.pipeline_state_dim,
                "action_dim": cfg.schema.pipeline_action_dim,
            },
            config_summary={
                "output_mode": cfg.output_mode,
                "num_workers": cfg.num_workers,
                "processed_episodes": len(episode_indices),
                "validity_mask_mode": "prefix_truncate_then_static_shorten",
                "state_action_alignment_enabled": align_cfg.enabled,
                "state_action_alignment_plan": p4_plan.name,
                "state_action_alignment_apply_delay": align_cfg.apply_delay,
                "state_action_alignment_lag": align_lag,
                "state_action_delay": delay_meta,
                "stage4_max_static_duration_sec": (cfg.stage4 or Stage4Config()).max_static_duration_sec,
                "stage4_max_static_steps": (cfg.stage4 or Stage4Config()).resolved_max_static_steps,
                "stage5_enabled": (cfg.stage5 or Stage5Config()).enabled,
                "embodiment": cfg.schema.embodiment,
                "ignored_episodes": len(ignored),
                "ignore_episodes_path": str(ignore_paths[0]) if len(ignore_paths) == 1 else None,
                "ignore_episodes_paths": [str(p) for p in ignore_paths],
            },
        )

    if timings is not None:
        timings["p3_pipeline_total"] = round(time.perf_counter() - started, 3)
    return results
