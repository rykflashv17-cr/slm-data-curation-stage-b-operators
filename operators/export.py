"""9. Standard Stage B shard export, reusing the upstream P0/P7 implementation."""

from __future__ import annotations

from pathlib import Path


class ExportOperator:
    def run(
        self,
        *,
        package_dir: Path,
        config_path: Path,
        output_dir: Path,
        sample_size: int | None = None,
        shard_id: int | None = None,
        num_shards: int | None = None,
        stats_cache_dir: Path | None = None,
        stats_only: bool = False,
        export_shard_only: bool = False,
        force_skip_gate: bool = False,
    ) -> int:
        from robot_data_processing.phases import orchestrator
        from ..pipeline import run_pipeline

        # Upstream owns standardization, media, metadata and P7. Replace only its P3
        # quality seam so all per-episode quality work runs through our operators.
        original = orchestrator.run_pipeline
        orchestrator.run_pipeline = run_pipeline
        try:
            return orchestrator.run_dataset_phases(
                package_dir=package_dir,
                config_path=config_path,
                output_dir=output_dir,
                sample_size=sample_size,
                shard_id=shard_id,
                num_shards=num_shards,
                stats_cache_dir=stats_cache_dir,
                stats_only=stats_only,
                export_shard_only=export_shard_only,
                force_skip_gate=force_skip_gate,
            )
        finally:
            orchestrator.run_pipeline = original
