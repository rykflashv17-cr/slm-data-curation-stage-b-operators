"""Stage B identity P1: pseudo-v3 EpisodeRef to standard and quality fields."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import StageBDatasetSpec


@dataclass(frozen=True)
class AlignedEpisode:
    ref: Any
    standard: Any
    state: np.ndarray
    action: np.ndarray
    parquet_path: Path


def build_dataset_adapter(spec: StageBDatasetSpec) -> Any:
    """Load an existing dataset adapter without assuming a package name."""

    path = spec.package_dir / "adapter.py"
    if not path.is_file():
        raise FileNotFoundError(f"Missing dataset adapter: {path}")
    if str(spec.package_dir) not in sys.path:
        sys.path.insert(0, str(spec.package_dir))
    name = "stage_b_adapter_" + spec.dataset_id.replace("/", "_").replace("-", "_")
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"Cannot import adapter from {path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    module_spec.loader.exec_module(module)
    if not hasattr(module, "build_adapter"):
        raise TypeError(f"Dataset adapter needs build_adapter(package_dir): {path}")
    return module.build_adapter(spec.package_dir)


class AlignedEpisodeOperator:
    """Read one Stage A output episode and apply the package's identity mapper.

    Raw-source ``load_raw`` and ``to_standard_episode`` are intentionally not used.
    The caller may provide an adapter and episode metadata map to amortize setup.
    """

    def run(
        self,
        spec: StageBDatasetSpec,
        ref: Any,
        *,
        adapter: Any | None = None,
        episode_rows: dict[int, dict[str, Any]] | None = None,
    ) -> AlignedEpisode:
        from robot_data_processing.loader import episode_parquet_path, read_episode_table
        from robot_data_processing.phases.orchestrator import _table_to_raw
        from robot_data_processing.v3_layout import episode_row_map

        dataset_root = Path(ref.dataset_root)
        path = episode_parquet_path(dataset_root, int(ref.episode_index))
        if not path.is_file():
            raise FileNotFoundError(f"Stage B episode Parquet not found: {path}")
        table = read_episode_table(path, episode_index=int(ref.episode_index))
        if table.num_rows == 0:
            raise ValueError(f"Empty Stage B episode after slice: {ref.key}")
        raw = _table_to_raw(table)
        adapter = adapter or build_dataset_adapter(spec)
        if not hasattr(adapter, "to_standard_episode_from_aligned"):
            raise TypeError(f"{spec.dataset_id} adapter lacks to_standard_episode_from_aligned")
        if not hasattr(adapter, "to_quality_arrays"):
            raise TypeError(f"{spec.dataset_id} adapter lacks to_quality_arrays")
        if episode_rows is None:
            episode_rows = episode_row_map(dataset_root)
        row = episode_rows.get(int(ref.episode_index)) or {}
        camera_map = (
            adapter.camera_name_map_from_root(dataset_root)
            if hasattr(adapter, "camera_name_map_from_root")
            else adapter.camera_name_map()
        )
        tasks = row.get("tasks") or ([ref.task] if ref.task else [])
        if isinstance(tasks, str):
            tasks = [tasks]
        meta: dict[str, Any] = {
            "fps": spec.fps,
            "embodiment": spec.embodiment,
            "dataset_root": str(dataset_root),
            "part": ref.part,
            "task": ref.task or (tasks[0] if tasks else None),
            "tasks": list(tasks),
            "source_episode_index": int(ref.episode_index),
            "camera_keys": list(camera_map.keys()),
            "input_format": "pseudo_v3",
            "identity_p1": True,
        }
        if row.get("camera_intrinsics") is not None:
            meta["camera_intrinsics"] = row["camera_intrinsics"]
        standard = adapter.to_standard_episode_from_aligned(ref, raw, meta)
        if int(standard.num_frames) != int(table.num_rows):
            raise ValueError(
                f"Aligned episode frame count changed for {ref.key}: "
                f"{standard.num_frames} != {table.num_rows}"
            )
        state, action = (np.asarray(value, dtype=np.float64) for value in adapter.to_quality_arrays(standard))
        if state.ndim != 2 or action.ndim != 2 or state.shape[0] != table.num_rows or action.shape[0] != table.num_rows:
            raise ValueError(f"Invalid quality array shape for {ref.key}: {state.shape}, {action.shape}")
        return AlignedEpisode(ref, standard, state, action, path)
