"""Execute Stage B quality and aligned shard export through upstream orchestration.

The upstream P0/P2/P7 code owns video, table and metadata synchronization. Its
P3 callable is replaced only during this invocation, so the v2 quality
operators produce the frame mask consumed by the existing exporter.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
import sys


class ExportOperator:
    def run(
        self,
        *,
        package_dir: Path,
        config_path: Path,
        output_dir: Path,
        sample_size: int | None = None,
        seed: int = 42,
        episode_range: tuple[int, int] | None = None,
        shard_id: int | None = None,
        num_shards: int | None = None,
        stats_cache_dir: Path | None = None,
        stats_only: bool = False,
        stats_phase: str | None = None,
        export_shard_only: bool = False,
        force_skip_gate: bool = False,
    ) -> int:
        import yaml

        from robot_data_processing import adapter_hooks
        from robot_data_processing.phases import orchestrator
        from ..pipeline import run_pipeline
        from .input_adapter import install_stage_b_quality_adapter

        package_dir = Path(package_dir).expanduser().resolve()
        config_path = Path(config_path).expanduser().resolve()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        dataset = raw.get("dataset") or {}
        pipeline = raw.get("pipeline") or {}
        if str(pipeline.get("mode", "")).lower() != "clean":
            raise ValueError("v2 only accepts pipeline.mode=clean (Stage B)")
        if str(dataset.get("input_format", "")).lower() != "pseudo_v3":
            raise ValueError("v2 expects Stage A pseudo_v3 input")
        if str(dataset.get("layout", "")).lower() != "single_root":
            raise ValueError("v2 expects a single_root Stage A dataset")
        if not (package_dir / "merge_shards.py").is_file():
            raise FileNotFoundError(package_dir / "merge_shards.py")

        # The current upstream orchestrator exposes P3 as a module-level
        # callable. Keep the replacement scoped and always restore it.
        # Package adapters may import sibling modules such as calibration.py.
        added_package_path = str(package_dir) not in sys.path
        if added_package_path:
            sys.path.insert(0, str(package_dir))
        original = orchestrator.run_pipeline
        original_install = adapter_hooks.install_adapter_hooks
        prior_adapter = adapter_hooks.get_adapter()
        prior_package = getattr(adapter_hooks, "_PACKAGE_DIR", None)

        def _install_stage_b_hooks(package):
            original_install(package)
            install_stage_b_quality_adapter()
            return adapter_hooks.get_adapter()

        orchestrator.run_pipeline = partial(run_pipeline, package_dir=package_dir)
        adapter_hooks.install_adapter_hooks = _install_stage_b_hooks
        try:
            return orchestrator.run_dataset_phases(
                package_dir=package_dir,
                config_path=config_path,
                output_dir=Path(output_dir).expanduser().resolve(),
                sample_size=sample_size,
                seed=seed,
                episode_range=episode_range,
                shard_id=shard_id,
                num_shards=num_shards,
                stats_cache_dir=stats_cache_dir,
                stats_only=stats_only,
                stats_phase=stats_phase,
                export_shard_only=export_shard_only,
                force_skip_gate=force_skip_gate,
            )
        finally:
            orchestrator.run_pipeline = original
            adapter_hooks.install_adapter_hooks = original_install
            adapter_hooks._ADAPTER = prior_adapter
            adapter_hooks._PACKAGE_DIR = prior_package
            if added_package_path and str(package_dir) in sys.path:
                sys.path.remove(str(package_dir))
