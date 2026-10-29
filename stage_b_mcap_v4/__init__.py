"""Three Stage B operators: MCAP reading, global statistics, quality detection."""

from .contracts import McapEpisodeRef, QualityDecision, QualityEpisode, SourceIdentity, StatisticsBundle
from .operators import GlobalStatsOperator, McapReadOperator, QualityDetectionOperator

__version__ = "0.1.0"

__all__ = [
    "McapEpisodeRef", "QualityEpisode", "SourceIdentity", "StatisticsBundle", "QualityDecision",
    "McapReadOperator", "GlobalStatsOperator", "QualityDetectionOperator",
]
