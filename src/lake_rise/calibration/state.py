"""Calibration state, candidates, versioned artifacts, and the audit log.

A `Candidate` is one proposed re-tuning (per-parameter deltas + confidence, the
multi-criteria acceptance table, the safety veto result, a one-time approval token). Approving
it promotes it to a new versioned artifact and moves the active-version pointer; everything is
recorded in an append-only audit log. Writes are atomic; state lives in its own file (never the
alerting state).
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..artifact import DEFAULT_ARTIFACT, load_artifact

DEFAULT_STATE_PATH = DEFAULT_ARTIFACT.parent / "calibration_state.json"
VERSIONS_PATH = DEFAULT_ARTIFACT.parent / "versions"


class ProposedParam(BaseModel):
    param: str
    current: float
    proposed: float | None
    prior: float | None = None
    confidence: str
    movement_from_prior: float | None = None
    warning: str = ""
    evidence: dict[str, Any] = {}

    @property
    def changed(self) -> bool:
        return (self.proposed is not None and self.confidence != "none"
                and abs(self.proposed - self.current) > 1e-9)


class Candidate(BaseModel):
    id: str
    created_at: str
    base_version: str
    params: list[ProposedParam]
    criteria: dict[str, Any] = {}       # multi-criteria table (water-balance/low-flow/peak/timing)
    anchors_pass: bool = True
    veto: dict[str, Any] = {}           # {passed, reason, ...}
    banner: str = ""
    token: str = ""

    @property
    def changed_params(self) -> list[ProposedParam]:
        return [p for p in self.params if p.changed]

    @property
    def acceptable(self) -> bool:
        return self.anchors_pass and self.veto.get("passed", False) and bool(self.changed_params)


class AuditEntry(BaseModel):
    at: str
    action: str                         # propose | approve | reject | revert
    version: str
    detail: str = ""
    metrics: dict[str, Any] = {}


class CalibrationState(BaseModel):
    active_version: str = "v0"
    pending: Candidate | None = None
    audit: list[AuditEntry] = []


# --- load / save (atomic) ----------------------------------------------------------------

def load_state(path: str | Path | None = None) -> CalibrationState:
    p = Path(path) if path is not None else DEFAULT_STATE_PATH
    if not p.exists():
        return CalibrationState()
    return CalibrationState.model_validate_json(p.read_text())


def save_state(state: CalibrationState, path: str | Path | None = None) -> None:
    p = Path(path) if path is not None else DEFAULT_STATE_PATH
    _atomic_write(p, state.model_dump_json(indent=2) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def new_token() -> str:
    return secrets.token_urlsafe(16)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- versioned artifacts -----------------------------------------------------------------

def active_artifact_path(state: CalibrationState, baseline: Path = DEFAULT_ARTIFACT,
                         versions_path: Path = VERSIONS_PATH) -> Path:
    """The artifact file for the currently active version. v0 = the canonical baseline."""
    if state.active_version in ("v0", "", None):
        return baseline
    return versions_path / f"crystal_lake_{state.active_version}.json"


def _next_version(versions_path: Path) -> str:
    existing = [p.stem.split("_")[-1] for p in versions_path.glob("crystal_lake_v*.json")]
    nums = [int(v[1:]) for v in existing if v.startswith("v") and v[1:].isdigit()]
    return f"v{(max(nums) + 1) if nums else 1}"


def _raw_set(data: dict, path: str, value: Any) -> None:
    node = data
    parts = path.split(".")
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def promote(candidate: Candidate, baseline: Path = DEFAULT_ARTIFACT,
            versions_path: Path = VERSIONS_PATH) -> str:
    """Write the candidate's changed parameters onto the active base artifact as a new version
    file (preserving inline comments via a raw-JSON edit), validate it, and return the version
    string. Raises if the written artifact fails to load."""
    versions_path.mkdir(parents=True, exist_ok=True)
    version = _next_version(versions_path)
    base_path = active_artifact_path(
        CalibrationState(active_version=candidate.base_version),
        baseline=baseline, versions_path=versions_path)
    raw = json.loads(base_path.read_text())
    for p in candidate.changed_params:
        _raw_set(raw, p.param, p.proposed)
    raw["version"] = version
    out = versions_path / f"crystal_lake_{version}.json"
    _atomic_write(out, json.dumps(raw, indent=2) + "\n")
    load_artifact(out)                      # final validation gate; raises on a bad artifact
    return version
