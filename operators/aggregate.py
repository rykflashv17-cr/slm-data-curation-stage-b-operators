"""10. Merge exported Stage B shards and compute final relative statistics."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


class AggregateOperator:
    def run(
        self,
        *,
        package_dir: Path,
        shard_dirs: list[Path],
        output_dir: Path,
        config_path: Path,
        output_layout: str = "aggregated_v3",
        merge_workers: int | None = None,
        relative_stats_workers: int | None = None,
    ) -> dict[str, Any]:
        if not shard_dirs:
            raise ValueError("At least one shard directory is required")
        script = Path(package_dir) / "merge_shards.py"
        if not script.is_file():
            raise FileNotFoundError(script)
        command = [sys.executable, str(script)]
        for shard in shard_dirs:
            command.extend(["--shard-dir", str(Path(shard).resolve())])
        command.extend([
            "--output-dir", str(Path(output_dir).resolve()),
            "--config", str(Path(config_path).resolve()),
            "--output-layout", output_layout,
        ])
        if merge_workers is not None:
            command.extend(["--merge-workers", str(merge_workers)])
        if relative_stats_workers is not None:
            command.extend(["--relative-stats-workers", str(relative_stats_workers)])
        subprocess.run(command, check=True)
        report = Path(output_dir) / "merge_report.json"
        if not report.is_file():
            raise RuntimeError(f"Upstream merge did not write {report}")
        return json.loads(report.read_text(encoding="utf-8"))
