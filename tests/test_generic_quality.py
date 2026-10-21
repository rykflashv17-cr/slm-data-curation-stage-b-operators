"""Stage-B pseudo-v3 quality parity and dataset-specific packing tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from robot_data_processing import adapter_hooks
from robot_data_processing.normalize.standard_types import StandardEpisode
from robot_data_processing.pipeline import pipeline_config_from_yaml
from robot_data_processing.stage1_stats import Stage1GlobalStats
from robot_data_processing.types import GlobalStats

from stage_b_operators_v2.contracts import StatisticsBundle
from stage_b_operators_v2.operators.input_adapter import InputOperator
from stage_b_operators_v2.pipeline import process_episode, run_pipeline


def _repo_root() -> Path:
    value = os.environ.get("SLM_DATA_CURATION_ROOT")
    if value:
        return Path(value)
    return Path(__file__).resolve().parents[2] / "upstream" / "SLM_data_curation"


def _arrow_column(value: np.ndarray):
    arr = np.asarray(value)
    if arr.ndim == 1:
        return pa.array(arr)
    return pa.FixedSizeListArray.from_arrays(pa.array(arr.reshape(-1)), arr.shape[1])


def _write_pseudo_v3(root: Path, episodes: list[dict[str, np.ndarray]]) -> None:
    rows = []
    for index, fields in enumerate(episodes):
        data_path = root / "data" / "chunk-000" / f"file-{index:03d}.parquet"
        data_path.parent.mkdir(parents=True, exist_ok=True)
        count = len(next(iter(fields.values())))
        table = pa.table({
            **{name: _arrow_column(value) for name, value in fields.items()},
            "episode_index": pa.array(np.full(count, index, dtype=np.int64)),
        })
        pq.write_table(table, data_path)
        rows.append({
            "episode_index": index,
            "data/chunk_index": 0,
            "data/file_index": index,
            "length": count,
        })
    meta_path = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), meta_path)
    (root / "meta" / "info.json").write_text(json.dumps({
        "codebase_version": "v3.0",
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
    }), encoding="utf-8")


def _ego_fields(*, count: int = 180, spike: bool = False) -> dict[str, np.ndarray]:
    x = np.linspace(0.0, 0.4, count, dtype=np.float32)
    state = np.column_stack((x, np.zeros(count), np.zeros(count))).astype(np.float32)
    action = np.roll(state, -1, axis=0)
    action[-1] = state[-1]
    if spike:
        state[150, 0] = 3.0
    rot = np.tile(np.array([1, 0, 0, 0, 1, 0], dtype=np.float32), (count, 1))
    grip = np.full(count, 0.5, dtype=np.float32)
    fields: dict[str, np.ndarray] = {}
    for side in ("left", "right"):
        fields[f"observation.state.eef.{side}.position"] = state
        fields[f"observation.state.eef.{side}.rotation_6d"] = rot
        fields[f"observation.state.gripper.{side}.closedness"] = grip
        fields[f"action.eef.{side}.position"] = action
        fields[f"action.eef.{side}.rotation_6d"] = rot
        fields[f"action.gripper.{side}.closedness"] = grip
    return fields


def _ur_fields(count: int = 180) -> dict[str, np.ndarray]:
    x = np.linspace(0.0, 0.2, count, dtype=np.float32)
    joint = np.repeat(x[:, None], 6, axis=1)
    action = np.roll(joint, -1, axis=0)
    action[-1] = joint[-1]
    grip = np.full(count, 0.5, dtype=np.float32)
    fields: dict[str, np.ndarray] = {}
    for side in ("left", "right"):
        fields[f"observation.state.arm.{side}.joint_position"] = joint
        fields[f"observation.state.gripper.{side}.closedness"] = grip
        fields[f"action.arm.{side}.joint_position"] = action
        fields[f"action.gripper.{side}.closedness"] = grip
    return fields


def _cfg(package: str, quality: str, root: Path, output: Path):
    raw = yaml.safe_load((_repo_root() / "datasets" / package / quality).read_text())
    raw.setdefault("dataset", {})["root"] = str(root)
    return pipeline_config_from_yaml(raw, {
        "dataset_root": str(root), "output_dir": str(output),
        "output_mode": "report", "num_workers": 1, "stats_num_bins": 256,
    })


def test_ego_episode_matches_upstream_on_real_pseudo_layout(tmp_path: Path) -> None:
    from robot_data_processing import pipeline as upstream

    source = tmp_path / "stage_a_aligned"
    _write_pseudo_v3(source, [_ego_fields(spike=True)])
    cfg = _cfg("Egostandard", "quality.yaml", source, tmp_path / "report")
    channels = cfg.schema.stage1_num_channels
    thresholds = np.full(channels, 1000.0, dtype=np.float64)
    stage1_stats = Stage1GlobalStats(thresholds, thresholds, thresholds, 180, 1, channels)
    lo = np.full(cfg.schema.pipeline_state_dim, -2.0)
    hi = np.full(cfg.schema.pipeline_state_dim, 2.0)
    stats = GlobalStats(lo, hi, lo, hi, lo, hi, lo, hi, 180, 1)
    bundle = StatisticsBundle(
        stats, stage1_stats, None, 1,
        tmp_path / "global.npz", tmp_path / "stage1.npz", tmp_path / "lag.npz",
    )
    upstream._init_worker(stats, stage1_stats, cfg, None)
    expected = upstream.process_episode(0, cfg)
    actual = process_episode(0, cfg, bundle)
    np.testing.assert_array_equal(actual.step_validity_mask, expected.step_validity_mask)
    assert actual.to_dict() == expected.to_dict()


def test_h5_quality_schema_reads_stage_a_parquet_not_raw_h5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "stage_a_aligned"
    _write_pseudo_v3(source, [_ur_fields()])
    cfg = _cfg("Robomindv2/ur_h5", "quality.yaml", source, tmp_path / "report")

    class RawH5Adapter:
        def load_quality_arrays(self, *args, **kwargs):
            raise AssertionError("raw H5 loader must not run in Stage B")

        def to_standard_episode_from_aligned(self, ref, raw, meta):
            return StandardEpisode(ref.episode_index, len(next(iter(raw.values()))), raw, meta=meta)

        def to_quality_arrays(self, standard):
            fields = standard.fields
            def pack(prefix):
                return np.column_stack((
                    fields[f"{prefix}.arm.left.joint_position"],
                    fields[f"{prefix}.gripper.left.closedness"],
                    fields[f"{prefix}.arm.right.joint_position"],
                    fields[f"{prefix}.gripper.right.closedness"],
                ))
            return pack("observation.state"), pack("action")

    fake = RawH5Adapter()
    monkeypatch.setattr(adapter_hooks, "_ADAPTER", fake)
    monkeypatch.setattr(adapter_hooks, "_PACKAGE_DIR", tmp_path)
    episode = InputOperator().run(0, cfg)
    assert episode is not None
    assert episode.state.shape == episode.action.shape == (180, 14)
    results = run_pipeline(cfg, [0], show_progress=False)
    assert len(results) == 1
    assert results[0].num_frames == 180
    assert adapter_hooks.get_adapter() is fake


def test_report_pipeline_matches_upstream_on_pseudo_v3(tmp_path: Path) -> None:
    from robot_data_processing import pipeline as upstream

    source = tmp_path / "stage_a_aligned"
    _write_pseudo_v3(source, [_ego_fields(), _ego_fields(spike=True)])
    old = _cfg("Egostandard", "quality.yaml", source, tmp_path / "old")
    new = _cfg("Egostandard", "quality.yaml", source, tmp_path / "new")
    expected = upstream.run_pipeline(old, [0, 1], show_progress=False)
    actual = run_pipeline(new, [0, 1], show_progress=False)
    assert [row.to_dict() for row in actual] == [row.to_dict() for row in expected]
    for got, want in zip(actual, expected):
        np.testing.assert_array_equal(got.step_validity_mask, want.step_validity_mask)
    assert (tmp_path / "new" / "reports" / "quality_report.json").is_file()
    assert (tmp_path / "new" / "reports" / "exclusion_log.jsonl").is_file()
