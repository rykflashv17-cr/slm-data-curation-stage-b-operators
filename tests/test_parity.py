"""Compare operatorized P3 against the upstream P3 on actual parquet episodes."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from robot_data_processing import pipeline as upstream
from robot_data_processing.stage1_stats import Stage1GlobalStats
from robot_data_processing.types import GlobalStats

from stage_b_operators.contracts import StatisticsBundle
from stage_b_operators.operators.export import ExportOperator
from stage_b_operators.pipeline import process_episode, run_pipeline


def _repo_root() -> Path:
    if os.environ.get("SLM_DATA_CURATION_ROOT"):
        return Path(os.environ["SLM_DATA_CURATION_ROOT"])
    packaged = Path(__file__).resolve().parents[2] / "upstream" / "SLM_data_curation"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[4] / "github" / "SLM_data_curation"


def _raw_episode(case: str, n: int = 180) -> tuple[np.ndarray, np.ndarray]:
    raw = np.zeros((n, 20), dtype=np.float32)
    xyz = np.linspace(0.0, 0.4, n, dtype=np.float32)
    raw[:, 0] = xyz
    raw[:, 10] = xyz * 0.8
    raw[:, 3] = raw[:, 13] = 1.0
    raw[:, 7] = raw[:, 17] = 1.0
    raw[:, 9] = raw[:, 19] = 0.5
    action = raw.copy()
    action[:-1] = raw[1:]
    if case == "extreme":
        raw[150, 0] = 3.0
    elif case == "static":
        raw[60:130] = raw[59]
        action[60:130] = action[59]
    elif case == "opposing":
        action[:, 0] = xyz[::-1]
        action[:, 10] = xyz[::-1] * 0.8
    elif case != "clean":
        raise ValueError(case)
    return raw, action


def _write_episode(root: Path, index: int, state: np.ndarray, action: np.ndarray) -> None:
    path = root / "data" / "chunk-000" / f"episode_{index:06d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = {
        "observation.state": pa.FixedSizeListArray.from_arrays(
            pa.array(state.reshape(-1)), state.shape[1]
        ),
        "action": pa.FixedSizeListArray.from_arrays(
            pa.array(action.reshape(-1)), action.shape[1]
        ),
    }
    pq.write_table(pa.table(columns), path)


@pytest.mark.parametrize("case", ["clean", "extreme", "static", "opposing"])
def test_episode_result_matches_upstream(tmp_path: Path, case: str) -> None:
    repo_root = _repo_root()
    quality = yaml.safe_load(
        (repo_root / "datasets" / "Egostandard" / "quality.yaml").read_text(encoding="utf-8")
    )
    cfg = upstream.pipeline_config_from_yaml(
        quality,
        {"dataset_root": str(tmp_path), "output_dir": str(tmp_path / "out"),
         "output_mode": "report", "num_workers": 1},
    )
    state, action = _raw_episode(case)
    _write_episode(tmp_path, 0, state, action)
    channels = cfg.schema.stage1_num_channels
    thresholds = np.full(channels, 1000.0, dtype=np.float64)
    stage1_stats = Stage1GlobalStats(thresholds, thresholds, thresholds, 180, 1, channels)
    lo = np.full(cfg.schema.pipeline_state_dim, -2.0)
    hi = np.full(cfg.schema.pipeline_state_dim, 2.0)
    global_stats = GlobalStats(lo, hi, lo, hi, lo, hi, lo, hi, 180, 1)
    bundle = StatisticsBundle(
        global_stats, stage1_stats, None, 1,
        tmp_path / "global.npz", tmp_path / "stage1.npz", tmp_path / "lag.npz",
    )

    upstream._init_worker(global_stats, stage1_stats, cfg, None)
    expected = upstream.process_episode(0, cfg)
    actual = process_episode(0, cfg, bundle)
    np.testing.assert_array_equal(actual.step_validity_mask, expected.step_validity_mask)
    assert actual.to_dict() == expected.to_dict()


def test_full_report_pipeline_matches_upstream(tmp_path: Path) -> None:
    repo_root = _repo_root()
    quality = yaml.safe_load(
        (repo_root / "datasets" / "Egostandard" / "quality.yaml").read_text(encoding="utf-8")
    )
    for index, case in enumerate(("clean", "static")):
        _write_episode(tmp_path, index, *_raw_episode(case))
    common = {"dataset_root": str(tmp_path), "output_mode": "report", "num_workers": 1,
              "stats_num_bins": 256}
    old_cfg = upstream.pipeline_config_from_yaml(
        quality, {**common, "output_dir": str(tmp_path / "old")}
    )
    new_cfg = upstream.pipeline_config_from_yaml(
        quality, {**common, "output_dir": str(tmp_path / "new")}
    )
    old = upstream.run_pipeline(old_cfg, [0, 1], show_progress=False)
    new = run_pipeline(new_cfg, [0, 1], show_progress=False)
    assert [row.to_dict() for row in new] == [row.to_dict() for row in old]
    for actual, expected in zip(new, old):
        np.testing.assert_array_equal(actual.step_validity_mask, expected.step_validity_mask)
    assert (tmp_path / "new" / "reports" / "quality_report.json").exists()
    assert (tmp_path / "new" / "reports" / "exclusion_log.jsonl").exists()


def test_export_operator_connects_to_upstream_orchestrator(tmp_path: Path) -> None:
    repo_root = _repo_root()
    package_dir = repo_root / "datasets" / "Egostandard"
    _write_episode(tmp_path, 0, *_raw_episode("clean"))
    quality = yaml.safe_load((package_dir / "quality.yaml").read_text(encoding="utf-8"))
    quality["stage3"]["stats"]["num_histogram_bins"] = 256
    quality_path = tmp_path / "quality.yaml"
    quality_path.write_text(yaml.safe_dump(quality), encoding="utf-8")
    config = yaml.safe_load((package_dir / "run_config_stage_b.yaml").read_text(encoding="utf-8"))
    config["dataset"]["root"] = str(tmp_path)
    config["dataset"]["input_format"] = "raw"
    config["pipeline"].update({"output_mode": "report", "num_workers": 1, "force_skip_gate": True})
    config["media"]["require_camera_top"] = False
    config["quality_config_path"] = str(quality_path)
    config_path = tmp_path / "run_config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    output = tmp_path / "operator_run"
    assert ExportOperator().run(
        package_dir=package_dir, config_path=config_path, output_dir=output,
    ) == 0
    assert (output / "_quality" / "reports" / "quality_report.json").exists()
