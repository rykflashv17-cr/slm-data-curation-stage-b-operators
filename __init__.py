"""Operatorized Stage B, backed by SLM_data_curation's existing algorithms."""

from .pipeline import process_episode, run_pipeline

__all__ = ["process_episode", "run_pipeline"]
