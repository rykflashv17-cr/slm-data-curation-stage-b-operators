"""Nine operators: MCAP input, stats, gripper gaps, Stage1-4, decision, output."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from .contracts import (
    CleaningDecision,
    GripperGapDecision,
    McapEpisodeRef,
    PrefixDecision,
    QualityEpisode,
    StaticDecision,
    StatisticsBundle,
)
from .spec import McapSpec, build_adapter, configure_source_environment, setup_imports


def _reference_times(path: Path, candidates: tuple[str, ...]) -> np.ndarray:
    from mcap.reader import make_reader

    timestamps: dict[str, list[int]] = {topic: [] for topic in candidates}
    with path.open("rb") as stream:
        for _schema, channel, message in make_reader(stream).iter_messages(topics=candidates):
            timestamps[channel.topic].append(int(message.log_time))
    for topic in candidates:
        if timestamps[topic]:
            return np.asarray(timestamps[topic], dtype=np.int64)
    raise ValueError(f"No configured reference topic in {path}: {candidates}")


class McapInputOperator:
    """Use the dataset adapter to construct quality arrays, retaining source clock."""

    def run(self, ref: McapEpisodeRef, spec: McapSpec, cfg, adapter) -> QualityEpisode:
        setup_imports(spec)
        from robot_data_processing.mask import (
            compute_per_joint_action_zero_exclude,
            compute_per_joint_stage1_exclude,
        )
        from robot_data_processing.normalize.standard_types import EpisodeRef
        from robot_data_processing.preprocess import expand_compact_exclude_to_action
        from robot_data_processing.transforms import robomind_ur_compact_teleop

        source_ref = EpisodeRef(
            episode_index=ref.episode_index,
            dataset_root=ref.dataset_root,
            part=ref.part or None,
            task=ref.task or None,
            rel_path=(ref.mcap_path.relative_to(ref.dataset_root).as_posix()
                      if spec.package_dir.name == "jindian_marvin" else ref.rel_path),
        )
        raw = adapter.load_raw(source_ref)
        standard = adapter.to_standard_episode(source_ref, raw, {
            "dataset_root": str(ref.dataset_root), "source_episode_index": ref.episode_index,
            "input_format": "pseudo_v3", "identity_p1": True,
        })
        state, action = (np.asarray(x, dtype=np.float64) for x in adapter.to_quality_arrays(standard))
        expected = (cfg.schema.pipeline_state_dim, cfg.schema.pipeline_action_dim)
        if state.ndim != 2 or action.ndim != 2 or (state.shape[1], action.shape[1]) != expected:
            raise ValueError(f"quality dimensions {(state.shape, action.shape)} != {expected}")
        native_times = _reference_times(ref.mcap_path, spec.reference_topics)
        stride = int(getattr(adapter, "_stride_by_ep", {}).get(ref.episode_index, 0))
        if stride < 1:
            stride = max(1, int(round(len(native_times) / max(len(state), 1))))
        frame_times = native_times[::stride]
        if len(frame_times) != len(state):
            raise ValueError(
                f"MCAP reference/quality alignment mismatch ep={ref.episode_index}: "
                f"{len(native_times)} source frames, stride={stride}, {len(state)} quality frames"
            )
        action_for_masks = action
        if cfg.schema.embodiment == "robomind_ur":
            _, action_for_masks = robomind_ur_compact_teleop(state, action)
        startup = compute_per_joint_action_zero_exclude(action_for_masks, cfg.action_zero_epsilon)
        stage1_exclude = compute_per_joint_stage1_exclude(
            action_for_masks, cfg.action_zero_epsilon, cfg.stage1_post_zero_grace_frames
        )
        if cfg.schema.embodiment == "robomind_ur":
            startup = expand_compact_exclude_to_action(startup, action.shape[1])
            stage1_exclude = expand_compact_exclude_to_action(stage1_exclude, action.shape[1])
        return QualityEpisode(ref, state, action, frame_times, startup, stage1_exclude)


class _McapStageBQualityHook:
    """Feed upstream global-statistics code its 32d Stage B quality space."""

    def __init__(self, spec: McapSpec, refs: list[McapEpisodeRef]) -> None:
        self.spec = spec
        self.adapter = build_adapter(spec)
        self.refs = {ref.episode_index: ref for ref in refs}

    def load_quality_arrays(self, root: Path, episode_index: int, schema=None, **kwargs):
        from robot_data_processing.normalize.standard_types import EpisodeRef

        del root, kwargs
        ref = self.refs[int(episode_index)]
        source_ref = EpisodeRef(
            episode_index=ref.episode_index, dataset_root=ref.dataset_root,
            part=ref.part or None, task=ref.task or None,
            rel_path=(ref.mcap_path.relative_to(ref.dataset_root).as_posix()
                      if self.spec.package_dir.name == "jindian_marvin" else ref.rel_path),
        )
        raw = self.adapter.load_raw(source_ref)
        standard = self.adapter.to_standard_episode(source_ref, raw, {
            "dataset_root": str(ref.dataset_root),
            "source_episode_index": ref.episode_index,
            "input_format": "pseudo_v3", "identity_p1": True,
        })
        state, action = self.adapter.to_quality_arrays(standard)
        state, action = np.asarray(state, dtype=np.float64), np.asarray(action, dtype=np.float64)
        if schema is not None and (state.shape[1], action.shape[1]) != (
            schema.pipeline_state_dim, schema.pipeline_action_dim
        ):
            raise ValueError(f"MCAP quality dimensions {(state.shape, action.shape)} mismatch schema")
        return {"state": state, "action": action}


class GlobalStatsOperator:
    """Compute the same upstream Stage1/3 statistics from the raw MCAP adapter."""

    def run(self, spec: McapSpec, cfg, refs: list[McapEpisodeRef], *, recompute: bool = False) -> StatisticsBundle:
        setup_imports(spec)
        from robot_data_processing import adapter_hooks
        from robot_data_processing.stage1_stats import load_or_compute_stage1_stats
        from robot_data_processing.state_action_lag_stats import load_or_compute_action_state_lag
        from robot_data_processing.stages.stage1_sudden_change import Stage1Config
        from robot_data_processing.stages.state_action_temporal_alignment import (
            StateActionAlignConfig, resolve_alignment_lag,
        )
        from robot_data_processing.stats import load_or_compute_stats

        if not refs:
            raise ValueError("global statistics require at least one MCAP episode")
        configure_source_environment(spec)
        previous_adapter = adapter_hooks.get_adapter()
        previous_package = getattr(adapter_hooks, "_PACKAGE_DIR", None)
        adapter_hooks._ADAPTER = _McapStageBQualityHook(spec, refs)
        adapter_hooks._PACKAGE_DIR = spec.package_dir
        cache = spec.output_root / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        ids = [ref.episode_index for ref in refs]
        fingerprint = hashlib.sha256()
        fingerprint.update(str(spec.input_root.resolve()).encode())
        fingerprint.update(spec.quality_yaml.read_bytes())
        fingerprint.update((spec.package_dir / "adapter.py").read_bytes())
        fingerprint.update(repr(spec.gripper_gap).encode())
        for ref in refs:
            fingerprint.update(f"{ref.episode_index}:{ref.rel_path}".encode())
            for source in (ref.mcap_path, ref.mcap_path.with_name("eef_pose.parquet")):
                if source.exists():
                    stat = source.stat()
                    fingerprint.update(f"{source.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        signature = fingerprint.hexdigest()
        manifest = cache / "source_fingerprint.json"
        if not recompute:
            try:
                cached = json.loads(manifest.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                cached = {}
            recompute = cached.get("signature") != signature
        try:
            global_stats = load_or_compute_stats(
                spec.input_root, cache / "global_stats.npz", ids, cfg.schema,
                recompute=recompute, num_workers=spec.workers,
                num_bins=cfg.stats_num_bins, show_progress=True,
            )
            stage1_stats = load_or_compute_stage1_stats(
                spec.input_root, cache / "stage1_global_stats.npz", ids,
                cfg.schema, cfg.stage1 or Stage1Config(), recompute=recompute,
                num_workers=spec.workers, num_bins=cfg.stats_num_bins, show_progress=True,
            )
            align_cfg = cfg.state_action_alignment or StateActionAlignConfig()
            align_stats = load_or_compute_action_state_lag(
                spec.input_root, cache / "state_action_lag.npz", ids, cfg.schema,
                align_cfg, recompute=recompute, num_workers=spec.workers, show_progress=True,
            )
            manifest.write_text(json.dumps({
                "signature": signature, "episodes": len(refs),
                "input_root": str(spec.input_root), "quality_yaml": str(spec.quality_yaml),
            }, indent=2), encoding="utf-8")
            return StatisticsBundle(
                global_stats, stage1_stats, align_stats,
                resolve_alignment_lag(align_cfg, align_stats), cache,
            )
        finally:
            adapter_hooks._ADAPTER = previous_adapter
            adapter_hooks._PACKAGE_DIR = previous_package


class Stage1Operator:
    def run(self, episode: QualityEpisode, cfg, statistics: StatisticsBundle):
        from robot_data_processing.stages.stage1_sudden_change import Stage1Config, run_stage1

        return run_stage1(
            episode.state, episode.action, cfg.stage1 or Stage1Config(),
            schema=cfg.schema, startup_exclude_per_joint=episode.stage1_exclude,
            global_stats=statistics.stage1_stats,
        )


class Stage2Operator:
    def run(self, episode: QualityEpisode, cfg, statistics: StatisticsBundle):
        from robot_data_processing.stages.stage2_trend_alignment import (
            Stage2Config, canonical_to_stage2_fields, run_stage2_on_fields,
        )

        if cfg.schema.embodiment == "jindian_marvin":
            # 7 joints per arm, followed by two gripper widths. The upstream
            # generic 32d mapper assumes 6 joints per arm and cannot represent
            # this source without silently dropping a joint.
            fields = {
                "observation.state.arm.left.joint_position": episode.state[:, :7],
                "observation.state.arm.right.joint_position": episode.state[:, 7:14],
                "action.arm.left.joint_position": episode.action[:, :7],
                "action.arm.right.joint_position": episode.action[:, 7:14],
            }
        else:
            fields = canonical_to_stage2_fields(
                episode.state, episode.action, embodiment=cfg.schema.embodiment
            )
        return run_stage2_on_fields(
            fields, cfg.stage2 or Stage2Config(), fill_lag=statistics.alignment_lag
        )


class Stage3Operator:
    def run(self, episode: QualityEpisode, cfg, statistics: StatisticsBundle):
        from robot_data_processing.stages.stage3_extreme_value import Stage3Config, run_stage3

        return run_stage3(
            episode.state, episode.action, statistics.global_stats,
            cfg.stage3 or Stage3Config(), schema=cfg.schema,
            startup_exclude_per_joint=episode.startup_exclude,
        )


def build_prefix(num_frames: int, stage1, stage2, stage3, cfg) -> PrefixDecision:
    """Shared policy function, deliberately not a ninth platform operator."""
    from robot_data_processing.mask import build_frame_keep_mask

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
    end = int(keep.sum()) if keep.any() else 0
    trigger = None
    if not stage2.discard and end < num_frames:
        firsts = {
            name: int(np.flatnonzero(flags)[0])
            for name, flags in (("stage1", stage1.abnormal_frames), ("stage3", stage3.remove_frames))
            if flags.any()
        }
        trigger = min(firsts, key=firsts.get) if firsts else None
        if trigger == "stage1" and stage1.rotation_joint_first_frame == firsts["stage1"]:
            trigger = "stage1_rotation_joint"
    return PrefixDecision(abnormal, keep, end, trigger, tuple(reasons))


class Stage4Operator:
    def run(self, episode: QualityEpisode, valid_end: int, cfg) -> StaticDecision:
        from robot_data_processing.stages.stage4_static_interval import Stage4Config, run_stage4

        result = run_stage4(
            episode.state[:valid_end], episode.action[:valid_end],
            cfg.stage4 or Stage4Config(),
        )
        remove = np.zeros(episode.num_frames, dtype=bool)
        remove[:valid_end] = result.remove_frames
        return StaticDecision(result, remove)


class GripperGapOperator:
    """Find frames without a sufficiently recent left or right gripper measurement.

    The nearest observation is used only to measure source coverage. Its value
    is never fabricated or used to declare a missing interval valid.
    """

    def run(self, episode: QualityEpisode, spec: McapSpec) -> GripperGapDecision:
        config = spec.gripper_gap
        n = episode.num_frames
        if config is None:
            return GripperGapDecision(np.ones(n, dtype=bool), {}, (), None)
        from mcap.reader import make_reader

        topics = {"left": config.left_topic, "right": config.right_topic}
        samples: dict[str, list[int]] = {topic: [] for topic in topics.values()}
        with episode.ref.mcap_path.open("rb") as stream:
            for _schema, channel, message in make_reader(stream).iter_messages(topics=list(samples)):
                samples[channel.topic].append(int(message.log_time))

        invalid: dict[str, np.ndarray] = {}
        ranges: list[dict[str, object]] = []
        times = episode.frame_times_ns
        terminal_dt = max(0, int(times[-1] - times[-2])) if n > 1 else 0
        for side, topic in topics.items():
            sample_times = np.sort(np.asarray(samples[topic], dtype=np.int64))
            if not len(sample_times):
                distance = np.full(n, np.iinfo(np.int64).max, dtype=np.int64)
            else:
                right = np.clip(np.searchsorted(sample_times, times), 0, len(sample_times) - 1)
                left = np.clip(right - 1, 0, len(sample_times) - 1)
                distance = np.minimum(np.abs(times - sample_times[left]), np.abs(times - sample_times[right]))
            bad = distance > config.max_nearest_gap_ns
            invalid[side] = bad
            for start, end in _true_runs(bad):
                ranges.append({
                    "side": side, "topic": topic,
                    "start_frame": start, "end_frame_exclusive": end,
                    "start_time_ns": int(times[start]),
                    "end_time_ns": int(times[end]) if end < n else int(times[-1]) + terminal_dt,
                    "max_nearest_gap_seconds": None if not len(sample_times) else float(distance[start:end].max() / 1e9),
                })
        valid = ~np.logical_or.reduce(tuple(invalid.values()))
        return GripperGapDecision(valid, invalid, tuple(ranges), config.max_nearest_gap_ns)


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[False, np.asarray(mask, dtype=bool), False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


class FinalDecisionOperator:
    def run(
        self, episode: QualityEpisode, cfg, statistics: StatisticsBundle,
        stage1, stage2, stage3, prefix: PrefixDecision, stage4: StaticDecision,
    ) -> CleaningDecision:
        from robot_data_processing.mask import build_frame_keep_mask
        from robot_data_processing.pipeline import tail_repeat_start
        from robot_data_processing.stages.stage1_sudden_change import EEF_SPIKE, GRIPPER_QUICK_REVERSAL

        n = episode.num_frames
        tail_start = tail_repeat_start(episode.frame_times_ns) if cfg.trim_tail_repeat_frames else n
        tail_remove = np.zeros(n, dtype=bool)
        tail_remove[tail_start:] = True
        mask = build_frame_keep_mask(n, prefix.abnormal_frames, stage4.remove_frames | tail_remove)
        kept = int(mask.sum())
        reasons = list(prefix.reasons)
        discard = bool(stage2.discard) or bool(stage3.discard)
        # Preserve the current upstream final-decision semantics exactly.
        if any(r.startswith((GRIPPER_QUICK_REVERSAL, EEF_SPIKE)) for r in stage1.discard_reasons):
            discard = True
        if cfg.discard_short_prefix and kept < cfg.min_episode_length:
            discard = True
            reasons.append(f"kept_frames={kept}<{cfg.min_episode_length}")
        return CleaningDecision(
            episode.ref, episode.frame_times_ns, mask, discard, tuple(reasons),
            {
                "num_frames": n, "kept_frames": kept,
                "stage1_flagged_frames": stage1.flagged_count,
                "stage2_da_mean": stage2.da_mean,
                "stage3_excluded_frames": stage3.excluded_count,
                "stage4_removed_frames": stage4.result.removed_count,
                "prefix_valid_end": prefix.valid_end,
                "prefix_trigger": prefix.trigger,
                "alignment_lag": statistics.alignment_lag,
            },
        )


class McapExportOperator:
    def run(self, decision: CleaningDecision, spec: McapSpec, *, overwrite: bool = False) -> Path | None:
        from .mcap_export import export_mcap

        return export_mcap(decision, spec, overwrite=overwrite)


def _judge_valid_segment(episode: QualityEpisode, cfg, stats: StatisticsBundle) -> CleaningDecision:
    s1 = Stage1Operator().run(episode, cfg, stats)
    s2 = Stage2Operator().run(episode, cfg, stats)
    s3 = Stage3Operator().run(episode, cfg, stats)
    prefix = build_prefix(episode.num_frames, s1, s2, s3, cfg)
    s4 = Stage4Operator().run(episode, prefix.valid_end, cfg)
    return FinalDecisionOperator().run(episode, cfg, stats, s1, s2, s3, prefix, s4)


def judge_one(
    episode: QualityEpisode, cfg, stats: StatisticsBundle,
    gripper_gap: GripperGapDecision | None = None,
) -> CleaningDecision:
    """Judge each sensor-valid run independently, then combine frame decisions."""
    if gripper_gap is None:
        return _judge_valid_segment(episode, cfg, stats)
    if gripper_gap.valid_mask.shape != (episode.num_frames,):
        raise ValueError("gripper gap result does not match episode length")
    gap_metrics = {
        "gripper_gap_invalid_frames": int(np.count_nonzero(~gripper_gap.valid_mask)),
        "gripper_gap_invalid_by_side": {
            side: int(np.count_nonzero(mask)) for side, mask in gripper_gap.invalid_by_side.items()
        },
        "gripper_gap_max_nearest_seconds": (
            None if gripper_gap.max_nearest_gap_ns is None else gripper_gap.max_nearest_gap_ns / 1e9
        ),
    }
    if gripper_gap.valid_mask.all():
        result = _judge_valid_segment(episode, cfg, stats)
        return replace(result, metrics={**result.metrics, **gap_metrics})

    keep = np.zeros(episode.num_frames, dtype=bool)
    reasons = [f"gripper_gap:{gap_metrics['gripper_gap_invalid_frames']} invalid frames"]
    segments: list[dict[str, object]] = []
    stage1_count = stage3_count = stage4_count = 0
    for start, end in _true_runs(gripper_gap.valid_mask):
        segment_info: dict[str, object] = {"start_frame": start, "end_frame_exclusive": end}
        if end - start < 2:
            segment_info["discarded"] = True
            segment_info["reasons"] = ["valid_segment_shorter_than_2_frames"]
            segments.append(segment_info)
            continue
        subepisode = QualityEpisode(
            episode.ref, episode.state[start:end], episode.action[start:end],
            episode.frame_times_ns[start:end], episode.startup_exclude[start:end],
            episode.stage1_exclude[start:end],
        )
        local = _judge_valid_segment(subepisode, cfg, stats)
        if not local.discard_episode:
            keep[start:end] = local.keep_mask
        else:
            reasons.extend(f"segment[{start}:{end}]:{reason}" for reason in local.reasons)
        stage1_count += int(local.metrics["stage1_flagged_frames"])
        stage3_count += int(local.metrics["stage3_excluded_frames"])
        stage4_count += int(local.metrics["stage4_removed_frames"])
        segment_info.update({
            "discarded": bool(local.discard_episode),
            "kept_frames": int(np.count_nonzero(local.keep_mask)) if not local.discard_episode else 0,
            "reasons": list(local.reasons),
            "stage2_da_mean": local.metrics["stage2_da_mean"],
        })
        segments.append(segment_info)
    return CleaningDecision(
        episode.ref, episode.frame_times_ns, keep, not bool(np.any(keep)), tuple(reasons),
        {
            "num_frames": episode.num_frames, "kept_frames": int(np.count_nonzero(keep)),
            "stage1_flagged_frames": stage1_count,
            "stage2_da_mean": None,
            "stage3_excluded_frames": stage3_count,
            "stage4_removed_frames": stage4_count,
            "alignment_lag": stats.alignment_lag,
            "valid_segments": segments,
            **gap_metrics,
        },
        gripper_gap.invalid_ranges,
    )
