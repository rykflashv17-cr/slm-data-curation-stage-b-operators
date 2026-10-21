"""P0 discovery and deterministic sampling for Stage A pseudo-v3 output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import StageBDatasetSpec


@dataclass(frozen=True)
class DiscoveryResult:
    """The selected shard and the full sample used for global statistics."""

    refs: list[Any]
    stats_refs: list[Any]
    quarantined: list[dict[str, Any]]
    input_root: Path

    @property
    def episode_indices(self) -> list[int]:
        return [int(ref.episode_index) for ref in self.refs]


class DiscoverOperator:
    """Match upstream P0 order: discover, quarantine, sample, range, shard."""

    def run(
        self,
        spec: StageBDatasetSpec,
        *,
        input_root: Path | None = None,
        sample_size: int | None = None,
        sample_mode: str | None = None,
        seed: int = 42,
        episode_range: tuple[int, int] | None = None,
        shard_id: int | None = None,
        num_shards: int | None = None,
    ) -> DiscoveryResult:
        import numpy as np

        from robot_data_processing.ignore_list import load_quarantine_entries, quarantine_match
        from robot_data_processing.phases.ingest import discover_single_root

        root = Path(input_root or spec.input_root).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"Stage B input root not found: {root}")
        dataset_cfg = spec.run_config.get("dataset") or {}
        pipeline_cfg = spec.run_config.get("pipeline") or {}
        total = dataset_cfg.get("total_episodes")
        refs = discover_single_root(root, int(total) if total is not None else None)

        quarantine_paths = [
            path if path.is_absolute() else spec.package_dir / path
            for path in (Path(p) for p in pipeline_cfg.get("quarantine_paths") or [])
        ]
        entries = load_quarantine_entries(quarantine_paths)
        quarantined: list[dict[str, Any]] = []
        if entries:
            selected = []
            for ref in refs:
                match = quarantine_match(ref, entries)
                if match is None:
                    selected.append(ref)
                else:
                    quarantined.append({**match, "matched_ref": ref.key})
            refs = selected

        if sample_size is not None:
            if sample_size < 1:
                raise ValueError("sample_size must be >= 1")
            if sample_size < len(refs):
                mode = str(sample_mode or pipeline_cfg.get("sample_mode", "random")).lower()
                if mode in ("sequential", "seq", "head", "first"):
                    refs = refs[:sample_size]
                elif mode in ("uniform", "even", "linspace", "strided"):
                    pick = np.unique(np.rint(np.linspace(0, len(refs) - 1, num=sample_size)).astype(int))
                    if len(pick) < sample_size:
                        used = set(int(i) for i in pick.tolist())
                        for i in range(len(refs)):
                            if i not in used:
                                used.add(i)
                                if len(used) >= sample_size:
                                    break
                        pick = np.array(sorted(used)[:sample_size], dtype=int)
                    refs = [refs[int(i)] for i in pick]
                else:
                    rng = np.random.default_rng(seed)
                    pick = sorted(rng.choice(len(refs), size=sample_size, replace=False).tolist())
                    refs = [refs[i] for i in pick]

        if episode_range is not None:
            start, end = episode_range
            if end <= start:
                raise ValueError("episode_range must be a nonempty half-open range")
            refs = [ref for ref in refs if start <= int(ref.episode_index) < end]
        stats_refs = list(refs)
        if (shard_id is None) != (num_shards is None):
            raise ValueError("shard_id and num_shards must be supplied together")
        if shard_id is not None:
            if num_shards is None or num_shards < 1 or not 0 <= shard_id < num_shards:
                raise ValueError(f"Invalid shard_id={shard_id}, num_shards={num_shards}")
            refs = [ref for i, ref in enumerate(refs) if i % num_shards == shard_id]
        return DiscoveryResult(refs, stats_refs, quarantined, root)
