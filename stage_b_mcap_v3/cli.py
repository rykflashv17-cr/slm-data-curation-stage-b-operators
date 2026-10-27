"""Small serial runner for the nine independent v3 operators."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .contracts import McapEpisodeRef
from .operators import GripperGapOperator, GlobalStatsOperator, McapExportOperator, McapInputOperator, judge_one
from .spec import build_adapter, discover, load_spec, quality_config


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _removed_ranges(mask: np.ndarray, times: np.ndarray) -> list[dict[str, int]]:
    out: list[dict[str, int]] = []
    terminal_dt = max(0, int(times[-1] - times[-2])) if len(times) > 1 else 0
    start = None
    for i, keep in enumerate([*mask.astype(bool), True]):
        if not keep and start is None:
            start = i
        elif keep and start is not None:
            out.append({
                "start_frame": start,
                "end_frame_exclusive": i,
                "start_time_ns": int(times[start]),
                "end_time_ns": int(times[i]) if i < len(times) else int(times[-1]) + terminal_dt,
            })
            start = None
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage B v3: MCAP in, cleaned MCAP out")
    parser.add_argument("command", choices=("stats", "quality", "run"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--package-dir", type=Path)
    parser.add_argument("--quality-yaml", type=Path)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--mcap", type=Path, action="append", help="Select one or more source episode.mcap files directly")
    parser.add_argument("--recompute-stats", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.sample_size is not None and args.sample_size < 1:
        parser.error("--sample-size must be positive")
    spec = load_spec(
        args.config, input_root=args.input_root, output_root=args.output_root,
        repo_root=args.repo_root, package_dir=args.package_dir,
        quality_yaml=args.quality_yaml,
    )
    spec.output_root.mkdir(parents=True, exist_ok=True)
    if args.mcap:
        refs = []
        for index, raw_path in enumerate(args.mcap[: args.sample_size] if args.sample_size else args.mcap):
            # Preserve the visible EEF tree path: its episode.mcap may be a
            # symlink into the original MCAP dataset.
            path = raw_path.expanduser().absolute()
            if path.suffix != ".mcap" or not path.is_file():
                raise ValueError(f"--mcap must point to an existing MCAP: {path}")
            try:
                rel = path.parent.relative_to(spec.input_root)
            except ValueError as exc:
                raise ValueError(f"--mcap must be below --input-root: {path}") from exc
            refs.append(McapEpisodeRef(
                index, spec.input_root, path, rel.as_posix(),
                rel.parts[1] if len(rel.parts) > 1 else "",
                rel.parts[2] if len(rel.parts) > 2 else "",
            ))
    else:
        refs = discover(spec, limit=args.sample_size)
    if not refs:
        raise ValueError(f"No MCAP episodes found under {spec.input_root}")
    cfg = quality_config(spec)
    stats = GlobalStatsOperator().run(spec, cfg, refs, recompute=args.recompute_stats)
    if args.command == "stats":
        print(json.dumps({
            "episodes": len(refs), "frames": stats.global_stats.num_frames,
            "cache_dir": str(stats.cache_dir),
        }, ensure_ascii=False))
        return 0
    adapter = build_adapter(spec)
    report_path = spec.output_root / "decisions.jsonl"
    exported = discarded = failed = 0
    with report_path.open("w", encoding="utf-8") as report:
        for ref in refs:
            try:
                episode = McapInputOperator().run(ref, spec, cfg, adapter)
                gaps = GripperGapOperator().run(episode, spec)
                decision = judge_one(episode, cfg, stats, gaps)
            except ValueError as exc:
                # Invalid or unsynchronised source streams should be visible
                # in the audit without terminating the other episodes.
                failed += 1
                report.write(json.dumps({
                    "episode_index": ref.episode_index,
                    "source": str(ref.mcap_path),
                    "output": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }, ensure_ascii=False) + "\n")
                report.flush()
                continue
            output = None
            if args.command == "run":
                output = McapExportOperator().run(decision, spec, overwrite=args.overwrite)
                exported += output is not None
            discarded += decision.discard_episode
            report.write(json.dumps({
                "episode_index": ref.episode_index,
                "source": str(ref.mcap_path),
                "output": str(output) if output else None,
                "discard_episode": decision.discard_episode,
                "reasons": decision.reasons,
                "metrics": decision.metrics,
                "gripper_gap_ranges": decision.gripper_gap_ranges,
                "removed_ranges": _removed_ranges(decision.keep_mask, decision.frame_times_ns),
            }, default=_json_default, ensure_ascii=False) + "\n")
            report.flush()
    summary = {
        "mode": args.command, "episodes": len(refs),
        "discarded_episodes": int(discarded), "exported_mcaps": int(exported),
        "failed_episodes": int(failed),
        "report": str(report_path),
    }
    (spec.output_root / "run_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0
