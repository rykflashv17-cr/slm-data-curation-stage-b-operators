"""P2 review gate for an upstream Stage B dataset package."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import StageBDatasetSpec


@dataclass(frozen=True)
class GateDecision:
    checklist_path: Path
    eef_direction: Any
    review: Any
    force_skip: bool
    require_for_export: bool
    export_extrinsics: bool
    extrinsic_confidence: str

    @property
    def effective_ok(self) -> bool:
        return self.force_skip or (
            bool(self.eef_direction.ok)
            and (not self.require_for_export or bool(self.review.ok))
        )


class GateOperator:
    """Evaluate the same front-loaded checks as the upstream orchestrator."""

    def run(
        self,
        spec: StageBDatasetSpec,
        *,
        force_skip: bool = False,
        output_mode: str | None = None,
    ) -> GateDecision:
        from robot_data_processing.gates.manual_gate import (
            assert_eef_direction_or_raise,
            assert_gate_or_raise,
            extrinsic_confidence_status,
            should_export_extrinsics,
        )

        checklist = spec.package_dir / "review_checklist.yaml"
        if not checklist.is_file():
            raise FileNotFoundError(f"Missing review checklist: {checklist}")
        pipeline = spec.run_config.get("pipeline") or {}
        skip = bool(force_skip or pipeline.get("force_skip_gate", False))
        mode = str(output_mode or pipeline.get("output_mode", "both")).lower()
        require_for_export = mode in ("filter", "both")
        eef = assert_eef_direction_or_raise(checklist, force_skip=skip)
        review = assert_gate_or_raise(
            checklist, force_skip=skip, require_for_export=require_for_export
        )
        return GateDecision(
            checklist_path=checklist,
            eef_direction=eef,
            review=review,
            force_skip=skip,
            require_for_export=require_for_export,
            export_extrinsics=should_export_extrinsics(checklist),
            extrinsic_confidence=extrinsic_confidence_status(checklist),
        )
