"""Discover Stage B dataset packages and resolve their existing YAML configs.

The registry is derived from the upstream repository at runtime.  It intentionally
does not contain a list of dataset names or data roots in this package.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StageBDatasetSpec:
    """Paths and settings needed to run one upstream Stage B package."""

    dataset_id: str
    repo_root: Path
    package_dir: Path
    config_path: Path
    quality_config_path: Path
    input_root: Path
    fps: float
    output_layout: str
    run_config: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.run_config["dataset"]["name"])

    @property
    def embodiment(self) -> str:
        return str(self.run_config["dataset"].get("embodiment", "unknown"))


def _repo_root(path: Path) -> Path:
    root = Path(path).expanduser().resolve()
    if not (root / "datasets").is_dir() or not (root / "qwen-manip-preprocess" / "src").is_dir():
        raise ValueError(f"Not an SLM_data_curation checkout: {root}")
    return root


def _load_config(path: Path) -> dict[str, Any]:
    import yaml

    with Path(path).open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def _quality_path(root: Path, package: Path, value: Any) -> Path:
    if not value:
        raise ValueError(f"Missing quality_config_path in {package}")
    raw = Path(str(value)).expanduser()
    choices = (raw,) if raw.is_absolute() else (root / raw, package / raw, raw)
    for candidate in choices:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Quality config {value!r} for {package} was not found; tried "
        + ", ".join(str(candidate) for candidate in choices)
    )


def _make_spec(
    root: Path,
    package: Path,
    config_path: Path,
    *,
    input_root: Path | None = None,
    output_layout: str | None = None,
) -> StageBDatasetSpec:
    config_path = Path(config_path).expanduser().resolve()
    config = _load_config(config_path)
    ds = config.get("dataset") or {}
    pipeline = config.get("pipeline") or {}
    export = config.get("export") or {}
    if not isinstance(ds, dict) or not isinstance(pipeline, dict) or not isinstance(export, dict):
        raise ValueError(f"Invalid Stage B config sections: {config_path}")
    if str(ds.get("input_format", "")).lower() != "pseudo_v3":
        raise ValueError(f"Stage B input_format must be pseudo_v3: {config_path}")
    if str(ds.get("layout", "")).lower() != "single_root":
        raise ValueError(f"Stage B layout must be single_root: {config_path}")
    if str(pipeline.get("mode", "")).lower() != "clean":
        raise ValueError(f"Stage B pipeline.mode must be clean: {config_path}")
    if not ds.get("name"):
        raise ValueError(f"Missing dataset.name in {config_path}")
    resolved_input = Path(input_root if input_root is not None else ds["root"]).expanduser()
    chosen_layout = str(output_layout or export.get("layout") or export.get("lerobot_version") or "").lower()
    if chosen_layout not in ("pseudo_v3", "aggregated_v3"):
        raise ValueError(f"Unsupported Stage B output layout {chosen_layout!r} in {config_path}")
    fps = float(ds.get("fps", 0))
    if fps <= 0:
        raise ValueError(f"Invalid dataset.fps={fps} in {config_path}")
    try:
        dataset_id = package.relative_to(root / "datasets").as_posix()
    except ValueError as exc:
        raise ValueError(f"Dataset package must be below {root / 'datasets'}: {package}") from exc
    config = deepcopy(config)
    config["dataset"]["root"] = str(resolved_input)
    config.setdefault("export", {})["layout"] = chosen_layout
    return StageBDatasetSpec(
        dataset_id=dataset_id,
        repo_root=root,
        package_dir=package,
        config_path=config_path,
        quality_config_path=_quality_path(root, package, config.get("quality_config_path")),
        input_root=resolved_input,
        fps=fps,
        output_layout=chosen_layout,
        run_config=config,
    )


def list_stage_b_datasets(repo_root: Path) -> dict[str, StageBDatasetSpec]:
    """Return every package with a Stage B run config, keyed by package path."""

    root = _repo_root(repo_root)
    specs: dict[str, StageBDatasetSpec] = {}
    for config_path in sorted((root / "datasets").rglob("run_config_stage_b.yaml")):
        package = config_path.parent
        spec = _make_spec(root, package, config_path)
        specs[spec.dataset_id] = spec
    return specs


def _select_dataset(
    specs: dict[str, StageBDatasetSpec], dataset: str
) -> StageBDatasetSpec:
    query = str(dataset).strip().replace("\\", "/").strip("/").casefold()
    if query.startswith("datasets/"):
        query = query[len("datasets/") :]
    matches = [
        spec
        for spec in specs.values()
        if query in (spec.dataset_id.casefold(), spec.package_dir.name.casefold(), spec.name.casefold())
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(
            f"Ambiguous dataset {dataset!r}; use one of: "
            + ", ".join(sorted(spec.dataset_id for spec in matches))
        )
    raise ValueError(
        f"Unknown Stage B dataset {dataset!r}; available: " + ", ".join(sorted(specs))
    )


def resolve_stage_b_dataset(
    repo_root: Path,
    dataset: str | None = None,
    *,
    config_path: Path | None = None,
    package_dir: Path | None = None,
    input_root: Path | None = None,
    output_layout: str | None = None,
) -> StageBDatasetSpec:
    """Resolve a package by path/name and optionally overlay a run config.

    An external custom YAML needs either ``dataset``/``package_dir`` or a unique
    ``dataset.name`` that matches a registered package.
    """

    root = _repo_root(repo_root)
    specs = list_stage_b_datasets(root)
    if not specs:
        raise FileNotFoundError(f"No Stage B dataset configs found under {root / 'datasets'}")
    selected = _select_dataset(specs, dataset) if dataset is not None else None
    if package_dir is not None:
        package = Path(package_dir).expanduser().resolve()
        package_matches = [spec for spec in specs.values() if spec.package_dir == package]
        if len(package_matches) != 1:
            raise ValueError(f"No Stage B config in package {package}")
        if selected is not None and selected.package_dir != package:
            raise ValueError(f"dataset={dataset!r} does not match package_dir={package}")
        selected = package_matches[0]
    custom = Path(config_path).expanduser().resolve() if config_path is not None else None
    if selected is None and custom is not None:
        parent_matches = [spec for spec in specs.values() if spec.package_dir == custom.parent]
        if len(parent_matches) == 1:
            selected = parent_matches[0]
        else:
            custom_name = str((_load_config(custom).get("dataset") or {}).get("name", ""))
            if custom_name:
                selected = _select_dataset(specs, custom_name)
    if selected is None:
        raise ValueError(
            "Specify a Stage B --dataset, --package-dir, or --config with a unique dataset.name; "
            "available: " + ", ".join(sorted(specs))
        )
    return _make_spec(
        root,
        selected.package_dir,
        custom or selected.config_path,
        input_root=input_root,
        output_layout=output_layout,
    )


class ConfigOperator:
    """Operator façade for the Stage B dataset/config resolution boundary."""

    def run(
        self,
        repo_root: Path,
        dataset: str | None = None,
        *,
        config_path: Path | None = None,
        package_dir: Path | None = None,
        input_root: Path | None = None,
        output_layout: str | None = None,
    ) -> StageBDatasetSpec:
        return resolve_stage_b_dataset(
            repo_root,
            dataset,
            config_path=config_path,
            package_dir=package_dir,
            input_root=input_root,
            output_layout=output_layout,
        )
