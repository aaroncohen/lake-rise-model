"""Small domain types for observed lake-gauge monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


OBSERVED_AVERAGE_MINUTES = 15


@dataclass(frozen=True)
class GaugeObservation:
    detected_at: datetime
    gauge_ft: float | None
    confirmed: bool
    sample_count: int
    degraded_reason: str | None = None

    @property
    def degraded(self) -> bool:
        return not self.confirmed
