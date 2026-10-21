"""Stage B operator interfaces, loaded on demand for fast catalog inspection."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "ConfigOperator": "config",
    "StageBDatasetSpec": "config",
    "list_stage_b_datasets": "config",
    "resolve_stage_b_dataset": "config",
    "GateOperator": "gate",
    "DiscoverOperator": "discover",
    "AlignedEpisodeOperator": "aligned",
    "ReportOperator": "report",
    "StatisticsOperator": "statistics",
    "InputOperator": "input_adapter",
    "Stage1Operator": "stage1",
    "Stage2Operator": "stage2",
    "Stage3Operator": "stage3",
    "PrefixOperator": "prefix",
    "Stage4Operator": "stage4",
    "FinalDecisionOperator": "decision",
    "QualityOperator": "quality",
    "ExportOperator": "export",
    "AggregateOperator": "aggregate",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value
