"""Check the full upstream Stage B package matrix, without reading dataset media.

Set SLM_DATA_CURATION_ROOT to a full StridingAI/SLM_data_curation checkout.
The adjacent historical EgoStandard-only snapshot is intentionally never used.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


# The current full upstream checkout has twelve explicit Stage B packages. A new
# run_config_stage_b.yaml must be added here so it cannot silently miss validation.
EXPECTED_LAYOUTS = {
    "AgibotWorld_Beta": "pseudo_v3",
    "Egostandard": "aggregated_v3",
    "Egoverse": "aggregated_v3",
    "InternDataA1_sim/franka": "pseudo_v3",
    "InternDataA1_sim/genie1": "pseudo_v3",
    "InternDataA1_sim/lift2": "pseudo_v3",
    "InternDataA1_sim/split_aloha": "pseudo_v3",
    "Robomindv2/agilex_h5": "aggregated_v3",
    "Robomindv2/ur_h5": "aggregated_v3",
    "abc_130k": "aggregated_v3",
    "egodex_lerobot_v21": "aggregated_v3",
    "humanoid_merged": "pseudo_v3",
}


@pytest.fixture(scope="module")
def upstream_root() -> Path:
    raw = os.environ.get("SLM_DATA_CURATION_ROOT")
    if not raw:
        pytest.fail(
            "Set SLM_DATA_CURATION_ROOT to the full upstream checkout; "
            "the adjacent historical snapshot contains only EgoStandard."
        )
    root = Path(raw).expanduser().resolve()
    assert (root / "specs" / "PIPELINE_PHASES.md").is_file(), root
    assert (root / "qwen-manip-preprocess" / "src" / "robot_data_processing").is_dir()
    return root


def test_all_stage_b_packages_are_in_the_matrix(upstream_root: Path) -> None:
    dataset_root = upstream_root / "datasets"
    found = {
        str(path.parent.relative_to(dataset_root))
        for path in dataset_root.rglob("run_config_stage_b.yaml")
    }
    assert found == set(EXPECTED_LAYOUTS), (
        f"Stage B package inventory changed: missing={sorted(set(EXPECTED_LAYOUTS) - found)}, "
        f"new={sorted(found - set(EXPECTED_LAYOUTS))}"
    )


@pytest.mark.parametrize("dataset,expected_layout", sorted(EXPECTED_LAYOUTS.items()))
def test_stage_b_package_config(
    upstream_root: Path, dataset: str, expected_layout: str
) -> None:
    package = upstream_root / "datasets" / dataset
    config = yaml.safe_load((package / "run_config_stage_b.yaml").read_text(encoding="utf-8"))
    assert config["dataset"]["input_format"] == "pseudo_v3"
    assert config["pipeline"]["mode"] == "clean"
    assert config["export"]["layout"] == expected_layout
    assert config["export"]["lerobot_version"] == expected_layout
    assert config["export"]["keep_all_frames"] is False
    assert config["export"]["v30"]["aggregate_media"] is (
        expected_layout == "aggregated_v3"
    )

    quality_path = Path(config["quality_config_path"])
    quality_candidates = (
        (upstream_root / quality_path, package / quality_path)
        if not quality_path.is_absolute()
        else (quality_path,)
    )
    assert any(path.is_file() for path in quality_candidates), quality_path
    for required in ("adapter.py", "run.py", "review_checklist.yaml", "merge_shards.py"):
        assert (package / required).is_file(), package / required


_ADAPTER_PROBE = """
from pathlib import Path
import sys
from robot_data_processing.adapter_hooks import install_adapter_hooks

package = Path(sys.argv[1])
adapter = install_adapter_hooks(package)
assert adapter is not None, f"No adapter loaded from {package}"
for name in ("to_standard_episode_from_aligned", "to_quality_arrays", "camera_name_map"):
    assert callable(getattr(adapter, name, None)), (package, name)
camera_map = adapter.camera_name_map()
assert isinstance(camera_map, dict) and camera_map, package
assert "camera_top" in camera_map.values(), (package, camera_map)
"""


@pytest.mark.parametrize("dataset", sorted(EXPECTED_LAYOUTS))
def test_stage_b_adapter_protocol(upstream_root: Path, dataset: str) -> None:
    """Isolate imports so package-local modules cannot leak between datasets."""
    engine = upstream_root / "qwen-manip-preprocess" / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(engine), env.get("PYTHONPATH"))))
    completed = subprocess.run(
        [sys.executable, "-c", _ADAPTER_PROBE, str(upstream_root / "datasets" / dataset)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"{dataset} adapter import/protocol failed:\n{completed.stdout}\n{completed.stderr}"
    )
