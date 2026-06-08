"""Fixture source: reads a snapshotted HA bundle from JSON (this milestone).

The snapshot is the wire format ``lake-rise pull`` writes and the live HA REST client
produces identically; both convert to an InputBundle through the shared
``bundle_from_snapshot`` so preprocessing can't diverge."""

from __future__ import annotations

import json
from pathlib import Path

from ..artifact import Artifact
from ..bundle import InputBundle
from .snapshot import bundle_from_snapshot


class FixtureSource:
    """Build an :class:`InputBundle` from a snapshot JSON file."""

    def __init__(self, art: Artifact, snapshot_path: str | Path):
        self.art = art
        self.snapshot_path = Path(snapshot_path)

    def build_bundle(self) -> InputBundle:
        snap = json.loads(self.snapshot_path.read_text())
        return bundle_from_snapshot(self.art, snap)
