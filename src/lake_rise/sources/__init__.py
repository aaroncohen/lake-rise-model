"""Data sources: anything that can produce an :class:`InputBundle`."""

from .base import DataSource
from .fixture import FixtureSource

__all__ = ["DataSource", "FixtureSource"]
