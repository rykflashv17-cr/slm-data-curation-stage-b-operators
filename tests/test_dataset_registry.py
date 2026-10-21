"""The Stage B registry must cover upstream packages without a hard-coded list."""

from __future__ import annotations

import os
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

PACKAGE_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "stage_b_dataset_registry_under_test", PACKAGE_DIR / "operators" / "config.py"
)
assert _spec is not None and _spec.loader is not None
_config = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _config
_spec.loader.exec_module(_config)
ConfigOperator = _config.ConfigOperator
list_stage_b_datasets = _config.list_stage_b_datasets
resolve_stage_b_dataset = _config.resolve_stage_b_dataset


def _repo_root() -> Path:
    configured = os.environ.get("SLM_DATA_CURATION_ROOT")
    if configured:
        return Path(configured)
    candidates = (
        Path(__file__).resolve().parents[2] / "upstream" / "SLM_data_curation",
        Path(__file__).resolve().parents[4] / "github" / "SLM_data_curation",
    )
    for path in candidates:
        if len(list((path / "datasets").rglob("run_config_stage_b.yaml"))) >= 12:
            return path
    pytest.skip("Upstream SLM_data_curation checkout is unavailable")


def test_registry_covers_all_stage_b_packages_and_quality_configs() -> None:
    root = _repo_root()
    specs = list_stage_b_datasets(root)
    config_paths = set((root / "datasets").rglob("run_config_stage_b.yaml"))
    assert {spec.config_path for spec in specs.values()} == {
        path.resolve() for path in config_paths
    }
    assert len(specs) == 12
    assert sum(spec.output_layout == "pseudo_v3" for spec in specs.values()) == 6
    assert sum(spec.output_layout == "aggregated_v3" for spec in specs.values()) == 6
    for dataset_id, spec in specs.items():
        assert dataset_id == spec.package_dir.relative_to(root / "datasets").as_posix()
        assert spec.quality_config_path.is_file()
        assert spec.run_config["dataset"]["input_format"] == "pseudo_v3"
        assert spec.run_config["pipeline"]["mode"] == "clean"


def test_resolve_nested_package_path_and_dataset_name() -> None:
    root = _repo_root()
    by_path = resolve_stage_b_dataset(root, "InternDataA1_sim/franka")
    by_name = resolve_stage_b_dataset(root, by_path.name.lower())
    by_config = resolve_stage_b_dataset(root, config_path=by_path.config_path)
    assert by_path.dataset_id == by_name.dataset_id == by_config.dataset_id
    assert by_path.quality_config_path.name == "quality_stage_b.yaml"
    assert ConfigOperator().run(root, "FrAnKa").dataset_id == by_path.dataset_id


def test_custom_config_and_cli_overrides_are_nonmutating(tmp_path: Path) -> None:
    root = _repo_root()
    original = resolve_stage_b_dataset(root, "Egostandard")
    copied = yaml.safe_load(original.config_path.read_text(encoding="utf-8"))
    copied["dataset"]["root"] = "/temporary/source"
    custom = tmp_path / "custom.yaml"
    custom.write_text(yaml.safe_dump(copied), encoding="utf-8")
    resolved = resolve_stage_b_dataset(
        root,
        config_path=custom,
        input_root=tmp_path / "aligned",
        output_layout="pseudo_v3",
    )
    assert resolved.dataset_id == "Egostandard"
    assert resolved.input_root == tmp_path / "aligned"
    assert resolved.output_layout == "pseudo_v3"
    assert resolved.run_config["dataset"]["root"] == str(tmp_path / "aligned")
    assert original.run_config["dataset"]["root"] != resolved.run_config["dataset"]["root"]
    assert original.output_layout == "aggregated_v3"


def test_stage_a_config_rejected() -> None:
    root = _repo_root()
    stage_a = root / "datasets" / "Egostandard" / "run_config_stage_a.yaml"
    with pytest.raises(ValueError, match="input_format must be pseudo_v3"):
        resolve_stage_b_dataset(root, "Egostandard", config_path=stage_a)
