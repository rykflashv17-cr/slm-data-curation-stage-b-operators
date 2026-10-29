"""Batch MCAP quality checking and audited whole-file rejection."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Any

import numpy as np

from .contracts import McapEpisodeRef
from .deletion import AuditWriteError, capture_source_identity, fsync_directory, write_json_durable
from .operators import GlobalStatsOperator, McapReadOperator, QualityDetectionOperator
from .spec import build_adapter, discover, load_spec, quality_config


REPORT_NAMES = ("decisions.jsonl", "deletion_plan.json", "deletion_events.jsonl", "run_summary.json")


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_decisions(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_json_safe(record), ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(path.parent)


def _select_refs(args, spec) -> list[McapEpisodeRef]:
    if args.mcap:
        refs = []
        selected = args.mcap[:args.sample_size] if args.sample_size else args.mcap
        for index, raw_path in enumerate(selected):
            path = raw_path.expanduser()
            path = path if path.is_absolute() else Path.cwd() / path
            try:
                rel = Path(os.path.normpath(path.parent)).relative_to(Path(os.path.normpath(spec.input_root)))
            except ValueError as exc:
                raise ValueError(f"--mcap must be below --input-root: {path}") from exc
            refs.append(McapEpisodeRef(
                index, spec.input_root, path, rel.as_posix(),
                rel.parts[1] if len(rel.parts) > 1 else "",
                rel.parts[2] if len(rel.parts) > 2 else "",
            ))
    else:
        refs = discover(spec, limit=args.sample_size)
    seen: set[Path] = set()
    seen_ids: set[int] = set()
    for ref in refs:
        source = ref.mcap_path.expanduser()
        source = source if source.is_absolute() else Path.cwd() / source
        try:
            normalized = Path(os.path.normpath(source))
            normalized.relative_to(Path(os.path.normpath(spec.input_root)))
        except ValueError as exc:
            raise ValueError(f"MCAP must be below --input-root: {source}") from exc
        if source.suffix != ".mcap" or not source.is_file():
            raise ValueError(f"MCAP must point to an existing file: {source}")
        if normalized in seen:
            raise ValueError(f"MCAP selected more than once: {source}")
        if ref.episode_index in seen_ids:
            raise ValueError(f"Episode index appears more than once: {ref.episode_index}")
        seen.add(normalized)
        seen_ids.add(ref.episode_index)
    if not refs:
        raise ValueError(f"No MCAP episodes found under {spec.input_root}")
    return refs


def _error_record(ref, exc, identity=None, blocked_reason=None) -> dict[str, Any]:
    return {
        "episode_index": ref.episode_index, "source": str(ref.mcap_path),
        "status": "error", "reject_file": False,
        "error": f"{type(exc).__name__}: {exc}",
        "retained_due_to_error": True,
        "source_identity": identity,
        "deletion_eligible": False,
        "deletion_blocked_reason": blocked_reason or "quality could not be determined",
    }


def _preflight(refs, spec, cfg, adapter):
    """Bound memory; exclude read errors and known gap failures from statistics."""
    identities: dict[int, dict[str, int] | None] = {}
    blocked: dict[int, str | None] = {}
    prepared: dict[int, dict[str, Any]] = {}
    valid = []
    reader = McapReadOperator()
    detector = QualityDetectionOperator()
    for ref in refs:
        identity, blocked_reason = None, None
        try:
            identity = capture_source_identity(ref.mcap_path, spec.input_root)
        except (OSError, ValueError) as exc:
            # A symlink can be inspected in quality mode, but never deleted.
            blocked_reason = f"{type(exc).__name__}: {exc}"
        identities[ref.episode_index], blocked[ref.episode_index] = identity, blocked_reason
        try:
            episode = reader.run(ref, spec, cfg, adapter)
            early_decision = detector.precheck(episode, spec)
            if identity is not None and capture_source_identity(ref.mcap_path, spec.input_root) != identity:
                raise ValueError("source identity changed while preflight reading")
            if early_decision is not None:
                record = _json_safe(early_decision.to_record())
                record.update({"source_identity": identity,
                               "deletion_eligible": identity is not None,
                               "deletion_blocked_reason": blocked_reason})
                prepared[ref.episode_index] = record
            else:
                valid.append(ref)
        except Exception as exc:
            prepared[ref.episode_index] = _error_record(ref, exc, identity, blocked_reason)
    return valid, identities, blocked, prepared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage B v4: reject a whole original MCAP on any quality failure")
    parser.add_argument("command", choices=("stats", "quality", "run"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--package-dir", type=Path)
    parser.add_argument("--quality-yaml", type=Path)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--mcap", type=Path, action="append", help="Select source MCAP files directly; repeat for multiple files")
    parser.add_argument("--recompute-stats", action="store_true")
    args = parser.parse_args(argv)
    if args.sample_size is not None and args.sample_size < 1:
        parser.error("--sample-size must be positive")
    started = time.perf_counter()
    spec = load_spec(
        args.config, input_root=args.input_root, output_root=args.output_root,
        repo_root=args.repo_root, package_dir=args.package_dir,
        quality_yaml=args.quality_yaml,
    )
    spec.output_root.mkdir(parents=True, exist_ok=True)
    if args.command != "stats":
        existing = [spec.output_root / name for name in REPORT_NAMES if (spec.output_root / name).exists() or (spec.output_root / name).is_symlink()]
        if existing:
            raise FileExistsError(f"Use a fresh --output-root; previous audit reports will not be overwritten: {existing[0]}")
    refs = _select_refs(args, spec)
    cfg = quality_config(spec)
    if args.command == "stats":
        statistics = GlobalStatsOperator().run(spec, cfg, refs, recompute=args.recompute_stats)
        print(json.dumps({"mode": "stats", "episodes": len(refs), "frames": statistics.global_stats.num_frames,
                          "cache_dir": str(statistics.cache_dir), "elapsed_seconds": time.perf_counter() - started}, ensure_ascii=False))
        return 0

    adapter = build_adapter(spec)
    valid_refs, identities, blocked, prepared = _preflight(refs, spec, cfg, adapter)
    records: dict[int, dict[str, Any]] = dict(prepared)
    statistics = None
    statistics_error = None
    if valid_refs:
        try:
            statistics = GlobalStatsOperator().run(spec, cfg, valid_refs, recompute=args.recompute_stats)
        except Exception as exc:
            statistics_error = f"{type(exc).__name__}: {exc}"
            for ref in valid_refs:
                records[ref.episode_index] = _error_record(ref, exc, identities[ref.episode_index], "global statistics failed; no source deletion")
    detector = QualityDetectionOperator()
    if statistics is not None:
        reader = McapReadOperator()
        for ref in valid_refs:
            identity, blocked_reason = identities[ref.episode_index], blocked[ref.episode_index]
            try:
                if identity is not None and capture_source_identity(ref.mcap_path, spec.input_root) != identity:
                    raise ValueError("source identity changed after batch preflight")
                episode = reader.run(ref, spec, cfg, adapter)
                decision = detector.run(episode, spec, cfg, statistics)
                if identity is not None and capture_source_identity(ref.mcap_path, spec.input_root) != identity:
                    raise ValueError("source identity changed during quality detection")
                record = _json_safe(decision.to_record())
                record.update({"source_identity": identity,
                               "deletion_eligible": identity is not None,
                               "deletion_blocked_reason": blocked_reason})
                records[ref.episode_index] = record
            except Exception as exc:
                records[ref.episode_index] = _error_record(ref, exc, identity, blocked_reason)
    ordered = [records[ref.episode_index] for ref in refs]
    rejected = [item for item in ordered if item["status"] == "rejected"]
    if statistics_error is not None:
        # A partial batch must not mutate sources when its shared baseline
        # could not be established, including already-diagnosed gripper gaps.
        for record in rejected:
            record["deletion_eligible"] = False
            record["deletion_blocked_reason"] = "batch global statistics failed; all source deletion blocked"
    plan = {
        "version": "v4", "mode": args.command,
        "input_root": str(spec.input_root),
        "policy": "delete_entire_original_mcap_if_any_quality_failure",
        "records": rejected,
        "rejected_files": len(rejected),
        "eligible_files": sum(bool(item["deletion_eligible"]) for item in rejected),
        "note": "No source deletion has occurred when this plan is committed. quality mode never applies it.",
    }
    # Both complete reports must be fsynced before the first source unlink.
    _write_decisions(spec.output_root / "decisions.jsonl", ordered)
    write_json_durable(spec.output_root / "deletion_plan.json", _json_safe(plan))
    deletion_results = []
    audit_error = None
    if args.command == "run":
        try:
            deletion_results = detector.delete_rejected(
                rejected, input_root=spec.input_root,
                events_path=spec.output_root / "deletion_events.jsonl",
            )
        except (AuditWriteError, OSError) as exc:
            # Never continue with further deletions after an audit failure.
            audit_error = f"{type(exc).__name__}: {exc}"
            deletion_results = getattr(exc, "completed_results", [])
    summary = {
        "version": "v4", "mode": args.command, "episodes": len(refs),
        "kept_files": sum(item["status"] == "kept" for item in ordered),
        "rejected_files": len(rejected),
        "failed_episodes": sum(item["status"] == "error" for item in ordered),
        "deleted_files": sum(item["status"] == "deleted" for item in deletion_results),
        "deletion_skipped_files": sum(item["status"] == "skipped" for item in deletion_results),
        "deleted_bytes": sum(item.get("deleted_bytes", 0) for item in deletion_results),
        "deletion_results": deletion_results,
        "statistics_error": statistics_error, "audit_error": audit_error,
        "report": str(spec.output_root / "decisions.jsonl"),
        "deletion_plan": str(spec.output_root / "deletion_plan.json"),
        "elapsed_seconds": time.perf_counter() - started,
    }
    write_json_durable(spec.output_root / "run_summary.json", _json_safe(summary))
    print(json.dumps(_json_safe(summary), ensure_ascii=False, allow_nan=False))
    return 1 if summary["failed_episodes"] or audit_error or summary["deletion_skipped_files"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
