#!/usr/bin/env python3
"""Run the operatorized Stage B or merge its exported shards."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _default_repo_root() -> Path | None:
    configured = os.environ.get("SLM_DATA_CURATION_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = (
        HERE.parent / "upstream" / "SLM_data_curation",
        HERE.parents[2] / "github" / "SLM_data_curation",
    )
    return next((path for path in candidates if path.is_dir()), None)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run stats, Stage1–4, P7 shard export")
    run.add_argument("--config", type=Path)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--input-root", type=Path)
    run.add_argument("--sample-size", type=int)
    run.add_argument("--workers", type=int)
    run.add_argument("--export-workers", type=int)
    run.add_argument("--output-mode", choices=("report", "filter", "both"))
    run.add_argument("--output-layout", choices=("pseudo_v3", "aggregated_v3"))
    run.add_argument("--shard-id", type=int)
    run.add_argument("--num-shards", type=int)
    run.add_argument("--stats-cache-dir", type=Path)
    run.add_argument("--stats-only", action="store_true")
    run.add_argument("--export-shard-only", action="store_true")
    run.add_argument("--force-skip-gate", action="store_true")

    quality = commands.add_parser("quality", help="Run operators 1–8 and write quality reports only")
    quality.add_argument("--config", type=Path, help="Quality YAML; defaults to Egostandard/quality.yaml")
    quality.add_argument("--input-root", type=Path, required=True)
    quality.add_argument("--output-dir", type=Path, required=True)
    quality.add_argument("--sample-size", type=int)
    quality.add_argument("--workers", type=int, default=4)

    merge = commands.add_parser("merge", help="Merge exported shards and relative stats")
    merge.add_argument("--config", type=Path)
    merge.add_argument("--shard-dir", type=Path, action="append", required=True)
    merge.add_argument("--output-dir", type=Path, required=True)
    merge.add_argument("--output-layout", choices=("pseudo_v3", "aggregated_v3"), default="aggregated_v3")
    merge.add_argument("--merge-workers", type=int)
    merge.add_argument("--relative-stats-workers", type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = args.repo_root
    if repo_root is None:
        raise SystemExit("Provide --repo-root or SLM_DATA_CURATION_ROOT")
    repo_root = repo_root.expanduser().resolve()
    package_dir = repo_root / "datasets" / "Egostandard"
    engine = repo_root / "qwen-manip-preprocess" / "src"
    if not package_dir.is_dir() or not engine.is_dir():
        raise SystemExit(f"Not an SLM_data_curation checkout: {repo_root}")
    sys.path.insert(0, str(engine))
    sys.path.insert(0, str(HERE.parent))
    default_config = package_dir / ("quality.yaml" if args.command == "quality" else "run_config_stage_b.yaml")
    config = (args.config or default_config).expanduser().resolve()

    from stage_b_operators.operators import AggregateOperator, ExportOperator

    if args.command == "quality":
        import yaml
        from robot_data_processing.loader import list_episode_indices
        from robot_data_processing.pipeline import pipeline_config_from_yaml
        from stage_b_operators.pipeline import run_pipeline

        if args.workers < 1 or (args.sample_size is not None and args.sample_size < 1):
            raise SystemExit("--workers and --sample-size must be >= 1")
        input_root = args.input_root.expanduser().resolve()
        indices = list_episode_indices(input_root)
        if args.sample_size is not None:
            indices = indices[:args.sample_size]
        if not indices:
            raise SystemExit(f"No episodes found in {input_root}")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        pcfg = pipeline_config_from_yaml(raw, {
            "dataset_root": str(input_root),
            "output_dir": str(args.output_dir.resolve()),
            "output_mode": "report",
            "num_workers": args.workers,
        })
        run_pipeline(pcfg, indices)
        return 0

    if args.command == "merge":
        AggregateOperator().run(
            package_dir=package_dir,
            shard_dirs=args.shard_dir,
            output_dir=args.output_dir,
            config_path=config,
            output_layout=args.output_layout,
            merge_workers=args.merge_workers,
            relative_stats_workers=args.relative_stats_workers,
        )
        return 0

    if (args.shard_id is None) != (args.num_shards is None):
        raise SystemExit("--shard-id and --num-shards must be supplied together")
    if args.num_shards is not None and (
        args.num_shards < 1 or not 0 <= args.shard_id < args.num_shards
    ):
        raise SystemExit("Invalid shard ID or shard count")
    if args.stats_only and args.stats_cache_dir is None:
        raise SystemExit("--stats-only requires --stats-cache-dir")
    if args.export_shard_only and args.shard_id is None:
        raise SystemExit("--export-shard-only requires shard arguments")
    if args.workers is not None and args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.export_workers is not None and args.export_workers < 1:
        raise SystemExit("--export-workers must be >= 1")

    overrides = args.input_root or args.workers or args.export_workers or args.output_mode or args.output_layout
    if overrides:
        import yaml

        raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        if args.input_root is not None:
            raw.setdefault("dataset", {})["root"] = str(args.input_root.expanduser().resolve())
        if args.workers is not None:
            raw.setdefault("pipeline", {})["num_workers"] = args.workers
        if args.export_workers is not None:
            raw.setdefault("pipeline", {})["export_workers"] = args.export_workers
        if args.output_mode is not None:
            raw.setdefault("pipeline", {})["output_mode"] = args.output_mode
        if args.output_layout is not None:
            export = raw.setdefault("export", {})
            export["layout"] = export["lerobot_version"] = args.output_layout
            export.setdefault("v30", {})["aggregate_media"] = args.output_layout == "aggregated_v3"
        args.output_dir.mkdir(parents=True, exist_ok=True)
        config = args.output_dir / "operator_runtime_config.yaml"
        config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    return ExportOperator().run(
        package_dir=package_dir, config_path=config, output_dir=args.output_dir,
        sample_size=args.sample_size, shard_id=args.shard_id,
        num_shards=args.num_shards, stats_cache_dir=args.stats_cache_dir,
        stats_only=args.stats_only, export_shard_only=args.export_shard_only,
        force_skip_gate=args.force_skip_gate,
    )


if __name__ == "__main__":
    raise SystemExit(main())
