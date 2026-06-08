"""The DataSource protocol. The fixture source (this milestone), the simulator,
and the future live HA REST client all implement it, so the predictor never knows
where its inputs came from (spec 6 portability)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..bundle import InputBundle


@runtime_checkable
class DataSource(Protocol):
    def build_bundle(self) -> InputBundle:
        """Assemble a normalized InputBundle in model units."""
        ...
