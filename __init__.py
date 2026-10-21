"""Dataset-selectable Stage B operators for SLM_data_curation."""

from __future__ import annotations

from importlib import import_module

__all__ = ["process_episode", "run_pipeline"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module(".pipeline", __name__), name)
    globals()[name] = value
    return value
