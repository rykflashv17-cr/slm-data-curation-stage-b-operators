#!/usr/bin/env python3
"""Run the generic SLM_data_curation Stage B operators."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _bootstrap_package() -> None:
    # multiprocessing spawn re-executes this file as __mp_main__; the alias
    # must exist before it unpickles stage_b_operators.pipeline workers.
    if HERE.name == "stage_b_operators":
        return
    spec = importlib.util.spec_from_file_location(
        "stage_b_operators", HERE / "__init__.py",
        submodule_search_locations=[str(HERE)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load operator package from {HERE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["stage_b_operators"] = module
    spec.loader.exec_module(module)


_bootstrap_package()


def _default_repo_root() -> Path | None:
    configured = os.environ.get("SLM_DATA_CURATION_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    # The v1 workspace snapshot contains only EgoStandard. Do not silently
    # advertise it as a checkout of the complete upstream Stage B.
    candidate = HERE.parent / "upstream" / "SLM_data_curation"
    if len(list((candidate / "datasets").rglob("run_config_stage_b.yaml"))) >= 12:
        return candidate
    return None


def _prepare_imports(repo_root: Path) -> None:
    engine = repo_root / "qwen-manip-preprocess" / "src"
    if not engine.is_dir():
        raise SystemExit(f"Not an SLM_data_curation checkout: {repo_root}")
    sys.path.insert(0, str(engine))
    sys.path.insert(0, str(HERE.parent))


def _dataset_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset", help="Package path, e.g. InternDataA1_sim/franka")
    parser.add_argument("--package-dir", type=Path, help="Dataset package directory")
    parser.add_argument("--config", type=Path, help="Stage B run_config_stage_b.yaml")


def _run_args(parser: argparse.ArgumentParser) -> None:
    _dataset_args(parser)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, help="Stage A pseudo_v3 dataset")
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--episode-range", type=int, nargs=2, metavar=("START", "STOP"))
    parser.add_argument("--workers", type=int)
    parser.add_argument("--export-workers", type=int)
    parser.add_argument("--output-mode", choices=("report", "filter", "both"))
    parser.add_argument("--output-layout", choices=("pseudo_v3", "aggregated_v3"))
    parser.add_argument("--shard-id", type=int)
    parser.add_argument("--num-shards", type=int)
    parser.add_argument("--stats-cache-dir", type=Path)
    parser.add_argument("--stats-only", action="store_true")
    parser.add_argument("--stats-phase", choices=(
        "pass1", "prepare_pass2", "pass2", "finalize",
        "resolution_census", "resolution_plan",
    ))
    parser.add_argument("--export-shard-only", action="store_true")
    parser.add_argument("--force-skip-gate", action="store_true")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List Stage B dataset packages in the upstream checkout")
    run = commands.add_parser("run", help="Run Stage B, optionally including filtered export")
    _run_args(run)
    quality = commands.add_parser("quality", help="Run Stage B in report mode without filtered video export")
    _run_args(quality)
    merge = commands.add_parser("merge", help="Merge Stage B shards and compute relative statistics")
    _dataset_args(merge)
    merge.add_argument("--shard-dir", type=Path, action="append", required=True)
    merge.add_argument("--output-dir", type=Path, required=True)
    merge.add_argument("--output-layout", choices=("pseudo_v3", "aggregated_v3"))
    merge.add_argument("--merge-workers", type=int)
    merge.add_argument("--relative-stats-workers", type=int)
    return parser.parse_args(argv)


def _validate_run_args(args: argparse.Namespace) -> None:
    if (args.shard_id is None) != (args.num_shards is None):
        raise SystemExit("--shard-id and --num-shards must be supplied together")
    if args.num_shards is not None and (
        args.num_shards < 1 or not 0 <= args.shard_id < args.num_shards
    ):
        raise SystemExit("Invalid shard ID or shard count")
    if args.shard_id is not None and args.stats_cache_dir is None:
        raise SystemExit("Sharded Stage B requires --stats-cache-dir from all selected episodes")
    for name in ("sample_size", "workers", "export_workers"):
        value = getattr(args, name)
        if value is not None and value < 1:
            raise SystemExit(f"--{name.replace('_', '-')} must be >= 1")
    if args.stats_only and args.stats_cache_dir is None:
        raise SystemExit("--stats-only requires --stats-cache-dir")
    if args.stats_phase and args.stats_cache_dir is None:
        raise SystemExit("--stats-phase requires --stats-cache-dir")
    if args.stats_phase in ("pass1", "pass2") and args.shard_id is None:
        raise SystemExit("pass1 and pass2 require --shard-id and --num-shards")
    if args.export_shard_only and args.shard_id is None:
        raise SystemExit("--export-shard-only requires --shard-id and --num-shards")
    if args.command == "quality" and any((
        args.stats_only, args.export_shard_only, args.stats_phase,
        args.shard_id is not None, args.stats_cache_dir is not None,
        args.export_workers is not None, args.output_layout is not None,
        args.output_mode not in (None, "report"),
    )):
        raise SystemExit("quality runs P3 only; use run for export, statistics or shard phases")


def _effective_config(spec, args: argparse.Namespace) -> Path:
    raw = copy.deepcopy(spec.run_config)
    changed = False
    if args.input_root is not None:
        raw.setdefault("dataset", {})["root"] = str(args.input_root.expanduser().resolve())
        changed = True
    for argument, key in (("workers", "num_workers"), ("export_workers", "export_workers")):
        value = getattr(args, argument)
        if value is not None:
            raw.setdefault("pipeline", {})[key] = value
            changed = True
    output_mode = "report" if args.command == "quality" else args.output_mode
    if output_mode is not None:
        raw.setdefault("pipeline", {})["output_mode"] = output_mode
        changed = True
    if args.output_layout is not None:
        export = raw.setdefault("export", {})
        export["layout"] = export["lerobot_version"] = args.output_layout
        export.setdefault("v30", {})["aggregate_media"] = args.output_layout == "aggregated_v3"
        changed = True
    if not changed:
        return spec.config_path
    import yaml

    args.output_dir.mkdir(parents=True, exist_ok=True)
    runtime_config = args.output_dir / "operator_runtime_config.yaml"
    runtime_config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return runtime_config


def _run_quality(spec, args: argparse.Namespace) -> int:
    """Run the per-episode quality operators without input video inspection."""
    import yaml

    from robot_data_processing import adapter_hooks
    from robot_data_processing.pipeline import pipeline_config_from_yaml
    from robot_data_processing.stages.stage2_trend_alignment import parse_stage2_fill_config
    from stage_b_operators.operators.discover import DiscoverOperator
    from stage_b_operators.operators.gate import GateOperator
    from stage_b_operators.operators.input_adapter import install_stage_b_quality_adapter
    from stage_b_operators.pipeline import run_pipeline

    GateOperator().run(spec, force_skip=args.force_skip_gate, output_mode="report")
    selection = DiscoverOperator().run(
        spec, input_root=args.input_root, sample_size=args.sample_size,
        seed=args.seed,
        episode_range=tuple(args.episode_range) if args.episode_range else None,
    )
    if not selection.refs:
        raise SystemExit("No Stage B episodes selected")
    quality = yaml.safe_load(spec.quality_config_path.read_text(encoding="utf-8")) or {}
    pipe = spec.run_config.get("pipeline") or {}
    overrides = {
        "dataset_root": str(selection.input_root),
        "output_dir": str(args.output_dir.expanduser().resolve()),
        "output_mode": "report",
        "num_workers": args.workers or int(pipe.get("num_workers", 1)),
        "temporal_align": spec.run_config.get("temporal_align") or {},
        "stage2_fill_missing_action_eef": parse_stage2_fill_config(
            spec.run_config, (quality.get("stage2") or {})
        ),
        "ignore_episodes_paths": list(pipe.get("ignore_episodes_paths") or []),
    }
    cfg = pipeline_config_from_yaml(quality, overrides)
    # Package adapters import sibling Python modules; install only for this
    # command and restore the process-global upstream registry afterward.
    package_path = str(spec.package_dir)
    added_package_path = package_path not in sys.path
    if added_package_path:
        sys.path.insert(0, package_path)
    prior_adapter = adapter_hooks.get_adapter()
    prior_package = getattr(adapter_hooks, "_PACKAGE_DIR", None)
    try:
        adapter_hooks.install_adapter_hooks(spec.package_dir)
        install_stage_b_quality_adapter()
        run_pipeline(
            cfg,
            [ref.episode_index for ref in selection.refs],
            stats_episode_indices=[ref.episode_index for ref in selection.stats_refs],
            package_dir=spec.package_dir,
        )
    finally:
        adapter_hooks._ADAPTER = prior_adapter
        adapter_hooks._PACKAGE_DIR = prior_package
        if added_package_path and package_path in sys.path:
            sys.path.remove(package_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.repo_root is None:
        raise SystemExit("Provide --repo-root or SLM_DATA_CURATION_ROOT for the complete upstream checkout")
    repo_root = args.repo_root.expanduser().resolve()
    _prepare_imports(repo_root)

    from stage_b_operators.operators.config import list_stage_b_datasets, resolve_stage_b_dataset

    if args.command == "list":
        catalog = list_stage_b_datasets(repo_root)
        print(json.dumps([
            {"dataset": name, "fps": spec.fps, "output_layout": spec.output_layout,
             "input_format": spec.run_config["dataset"]["input_format"]}
            for name, spec in catalog.items()
        ], indent=2, ensure_ascii=False))
        return 0

    spec = resolve_stage_b_dataset(
        repo_root, args.dataset, package_dir=args.package_dir, config_path=args.config
    )
    from stage_b_operators.operators.aggregate import AggregateOperator
    from stage_b_operators.operators.export import ExportOperator

    if args.command == "merge":
        report = AggregateOperator().run(
            package_dir=spec.package_dir, shard_dirs=args.shard_dir,
            output_dir=args.output_dir, config_path=spec.config_path,
            output_layout=args.output_layout or spec.output_layout,
            merge_workers=args.merge_workers,
            relative_stats_workers=args.relative_stats_workers,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    _validate_run_args(args)
    if args.command == "quality":
        return _run_quality(spec, args)
    config_path = _effective_config(spec, args)
    return ExportOperator().run(
        package_dir=spec.package_dir, config_path=config_path,
        output_dir=args.output_dir, sample_size=args.sample_size, seed=args.seed,
        episode_range=tuple(args.episode_range) if args.episode_range else None,
        shard_id=args.shard_id, num_shards=args.num_shards,
        stats_cache_dir=args.stats_cache_dir, stats_only=args.stats_only,
        stats_phase=args.stats_phase, export_shard_only=args.export_shard_only,
        force_skip_gate=args.force_skip_gate,
    )


if __name__ == "__main__":
    raise SystemExit(main())
