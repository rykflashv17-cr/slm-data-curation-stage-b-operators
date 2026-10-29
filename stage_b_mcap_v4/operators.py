"""Three registered operators: MCAP reading, global statistics, quality detection."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .contracts import (
    McapEpisodeRef, QualityDecision, QualityEpisode, SourceIdentity,
    StatisticsBundle, frame_ranges,
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


class McapReadOperator:
    """Use the dataset adapter to construct quality arrays, retaining source clock."""

    def run(self, ref: McapEpisodeRef, spec: McapSpec, cfg, adapter) -> QualityEpisode:
        identity = SourceIdentity.capture(ref.mcap_path)
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
            rel_path=(Path(os.path.relpath(ref.mcap_path, ref.dataset_root)).as_posix()
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
        if not len(state):
            raise ValueError("MCAP contains no quality frames")
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
        if SourceIdentity.capture(ref.mcap_path) != identity:
            raise ValueError("source MCAP changed while reading")
        return QualityEpisode(ref, state, action, frame_times, startup, stage1_exclude, identity)


class _McapStageBQualityHook:
    """Feed upstream statistics the source profile quality arrays."""

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
            rel_path=(Path(os.path.relpath(ref.mcap_path, ref.dataset_root)).as_posix()
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
        if not np.isfinite(state).all() or not np.isfinite(action).all():
            raise ValueError("non-finite arrays cannot be used to fit global statistics")
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
        fingerprint.update(b"stage_b_v4_whole_file_policy")
        fingerprint.update(json.dumps(spec.quality_overrides, sort_keys=True).encode())
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


def _gripper_gap(episode: QualityEpisode, spec: McapSpec) -> tuple[np.ndarray, dict]:
    """Measure timestamp coverage without declaring missing readings valid."""
    bad = np.zeros(episode.num_frames, dtype=bool)
    config = spec.gripper_gap
    if config is None:
        return bad, {"status": "disabled", "detected_ranges": []}
    from mcap.reader import make_reader

    topics = {"left": config.left_topic, "right": config.right_topic}
    samples: dict[str, list[int]] = {topic: [] for topic in topics.values()}
    with episode.ref.mcap_path.open("rb") as stream:
        for _schema, channel, message in make_reader(stream).iter_messages(topics=list(samples)):
            samples[channel.topic].append(int(message.log_time))
    ranges = []
    counts = {}
    for side, topic in topics.items():
        observed = np.sort(np.asarray(samples[topic], dtype=np.int64))
        if not len(observed):
            side_bad = np.ones(episode.num_frames, dtype=bool)
            distances = None
        else:
            right = np.clip(np.searchsorted(observed, episode.frame_times_ns), 0, len(observed) - 1)
            left = np.maximum(right - 1, 0)
            distances = np.minimum(
                np.abs(episode.frame_times_ns - observed[left]),
                np.abs(episode.frame_times_ns - observed[right]),
            )
            side_bad = distances > config.max_nearest_gap_ns
        bad |= side_bad
        counts[side] = int(side_bad.sum())
        for interval in frame_ranges(side_bad, episode.frame_times_ns):
            start, end = interval["start_frame"], interval["end_frame_exclusive"]
            ranges.append({
                **interval, "side": side, "topic": topic,
                "max_nearest_gap_seconds": float(distances[start:end].max() / 1e9) if distances is not None else None,
            })
    return bad, {
        "status": "rejected" if bad.any() else "passed",
        "bad_frames": int(bad.sum()), "invalid_frames_by_side": counts,
        "max_nearest_gap_seconds": config.max_nearest_gap_ns / 1e9,
        "detected_ranges": ranges,
    }


def _stage_result(mask: np.ndarray, times: np.ndarray, *, discard: bool = False,
                  reasons=(), **metrics) -> dict:
    return {
        "status": "rejected" if discard or mask.any() else "passed",
        "discard": bool(discard), "bad_frames": int(np.count_nonzero(mask)),
        "reasons": list(reasons), "detected_ranges": frame_ranges(mask, times),
        **metrics,
    }


class QualityDetectionOperator:
    """Gripper coverage and Stage1–4; one failure rejects the whole source MCAP."""

    def precheck(self, episode: QualityEpisode, spec: McapSpec) -> QualityDecision | None:
        """A coverage failure needs no fitted statistics or numerical scoring."""
        if not episode.num_frames:
            raise ValueError("cannot judge an episode without quality frames")
        mask, gap = _gripper_gap(episode, spec)
        if not mask.any():
            return None
        stages = {"gripper_gap": gap}
        stages.update({name: {"status": "skipped", "reason": "gripper_gap_rejected_entire_file", "detected_ranges": []}
                       for name in ("s1", "s2", "s3", "s4")})
        return QualityDecision(
            episode.ref, episode.frame_times_ns, mask, True,
            (f"gripper_gap:invalid_frames={int(mask.sum())}",), stages,
            episode.source_identity,
        )

    def run(self, episode: QualityEpisode, spec: McapSpec, cfg,
            statistics: StatisticsBundle) -> QualityDecision:
        early = self.precheck(episode, spec)
        if early is not None:
            return early
        if not np.isfinite(episode.state).all() or not np.isfinite(episode.action).all():
            raise ValueError("non-finite quality values without a diagnosed gripper coverage gap")
        setup_imports(spec)
        from robot_data_processing.stages.stage1_sudden_change import Stage1Config, run_stage1
        from robot_data_processing.stages.stage2_trend_alignment import (
            Stage2Config, canonical_to_stage2_fields, run_stage2_on_fields,
        )
        from robot_data_processing.stages.stage3_extreme_value import Stage3Config, run_stage3
        from robot_data_processing.stages.stage4_static_interval import Stage4Config, run_stage4

        n, times = episode.num_frames, episode.frame_times_ns
        s1 = run_stage1(
            episode.state, episode.action, cfg.stage1 or Stage1Config(),
            schema=cfg.schema, startup_exclude_per_joint=episode.stage1_exclude,
            global_stats=statistics.stage1_stats,
        )
        if cfg.schema.embodiment == "jindian_marvin":
            fields = {
                "observation.state.arm.left.joint_position": episode.state[:, :7],
                "observation.state.arm.right.joint_position": episode.state[:, 7:14],
                "action.arm.left.joint_position": episode.action[:, :7],
                "action.arm.right.joint_position": episode.action[:, 7:14],
            }
        else:
            fields = canonical_to_stage2_fields(episode.state, episode.action, embodiment=cfg.schema.embodiment)
        s2 = run_stage2_on_fields(fields, cfg.stage2 or Stage2Config(), fill_lag=statistics.alignment_lag)
        s3 = run_stage3(
            episode.state, episode.action, statistics.global_stats,
            cfg.stage3 or Stage3Config(), schema=cfg.schema,
            startup_exclude_per_joint=episode.startup_exclude,
        )
        # With whole-file rejection there is no surviving prefix to cut/export.
        # Evaluate S4 on the original episode so its evidence is independently visible.
        stage4_cfg = cfg.stage4 or Stage4Config()
        s4 = run_stage4(episode.state, episode.action, stage4_cfg)
        masks = {
            "s1": np.asarray(s1.abnormal_frames, dtype=bool),
            "s2": np.full(n, bool(s2.discard), dtype=bool),
            "s3": np.asarray(s3.remove_frames, dtype=bool),
            "s4": np.asarray(s4.remove_frames, dtype=bool),
        }
        if any(mask.shape != (n,) for mask in masks.values()):
            raise ValueError("a quality stage returned a mask with the wrong length")
        reports = {
            "gripper_gap": {"status": "passed" if spec.gripper_gap else "disabled", "bad_frames": 0, "detected_ranges": []},
            "s1": _stage_result(
                masks["s1"], times, discard=s1.discard, reasons=s1.discard_reasons,
                flagged_count=int(s1.flagged_count), hard_limit_hit=bool(s1.hard_limit_hit),
                gripper_faults=dict(s1.gripper_faults), rotation_joint_steps=int(s1.rotation_joint_steps),
            ),
            "s2": _stage_result(
                masks["s2"], times, discard=s2.discard, reasons=s2.discard_reasons,
                da_mean=s2.da_mean, da_per_dim=s2.da_per_dim,
                dim_names=s2.dim_names, lags=s2.lags,
                episode_lag=s2.episode_lag, lag_source=s2.lag_source,
            ),
            "s3": _stage_result(
                masks["s3"], times, discard=s3.discard, reasons=s3.discard_reasons,
                excluded_count=int(s3.excluded_count),
            ),
            "s4": _stage_result(
                masks["s4"], times, removed_count=int(s4.removed_count),
                static_runs_shortened=int(s4.static_runs_shortened),
                static_frames_removed=int(s4.static_frames_removed),
            ),
        }
        if not stage4_cfg.enabled:
            reports["s4"]["status"] = "disabled"
        reasons = []
        for name, result in reports.items():
            if result["status"] != "rejected":
                continue
            reasons.append(f"{name}:bad_frames={result['bad_frames']}")
            reasons.extend(f"{name}:{reason}" for reason in result.get("reasons", ()))
        bad = np.logical_or.reduce(list(masks.values()))
        return QualityDecision(
            episode.ref, times, bad, bool(reasons), tuple(reasons), reports,
            episode.source_identity,
        )

    def delete_rejected(self, plan_records: list[dict], *, input_root: Path,
                        events_path: Path) -> list[dict]:
        """Apply recorded file decisions; deletion belongs to this operator."""
        from .deletion import delete_rejected

        return delete_rejected(plan_records, input_root=input_root, events_path=events_path)
