"""Write the upstream Stage B quality and exclusion reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ReportOperator:
    """EpisodeResult list to the three standard quality report artifacts."""

    def run(
        self,
        results: list[Any],
        output_dir: Path,
        *,
        stats_meta: dict[str, Any] | None = None,
        config_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from robot_data_processing.report import (
            build_quality_report,
            write_exclusion_log,
            write_optimal_lags,
            write_quality_report,
        )

        reports = Path(output_dir) / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        report = build_quality_report(
            results,
            stats_meta=stats_meta or {},
            config_summary=config_summary or {"phase": "P3", "output_mode": "report"},
        )
        write_quality_report(reports / "quality_report.json", report)
        write_exclusion_log(reports / "exclusion_log.jsonl", results)
        write_optimal_lags(reports / "optimal_lags.json", results)
        return report
