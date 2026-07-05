"""Orchestration: run a training proposal, and approve / reject / revert it. Pure of the
CLI/HTTP layers so both can drive the same logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import storm_record as SR
from ..artifact import DEFAULT_ARTIFACT, load_artifact
from ..registry import load_registry
from . import archive, train
from .config import CalibrationConfig
from .state import (
    AuditEntry,
    Candidate,
    CalibrationState,
    active_artifact_path,
    load_state,
    now_iso,
    promote,
    save_state,
)


def active_artifact(config: CalibrationConfig):
    """The currently active (live) model artifact."""
    state = load_state(config.state_path)
    return load_artifact(active_artifact_path(state, versions_path=config.versions_path))


def run_training(config: CalibrationConfig, continuous_path: str | Path | None = None,
                 storms_path: str | Path | None = None) -> Candidate:
    """Extract signatures from the archived data, build a graded Candidate against the active
    model, record it as the pending proposal, and return it."""
    state = load_state(config.state_path)
    art = load_artifact(active_artifact_path(state, versions_path=config.versions_path))
    reg = load_registry()
    continuous = archive.load(continuous_path)
    storms = SR.load_dataset(storms_path)

    candidate = train.train(
        art, reg, continuous, storms, bfi_target=config.bfi_target,
        min_recession_days=config.min_recession_days,
    )
    state.pending = candidate
    state.audit.append(AuditEntry(
        at=now_iso(), action="propose", version=candidate.base_version,
        detail=candidate.banner,
        metrics={"changed": [p.param for p in candidate.changed_params],
                 "anchors_pass": candidate.anchors_pass, "veto": candidate.veto.get("passed")},
    ))
    save_state(state, config.state_path)
    return candidate


def approve(config: CalibrationConfig, candidate_id: str, token: str,
            baseline: Path = DEFAULT_ARTIFACT) -> str:
    """Promote the pending candidate (single-use token) to a new active version. Raises on a
    mismatched id/token, an unacceptable candidate, or a bad write."""
    state = load_state(config.state_path)
    c = state.pending
    if c is None or c.id != candidate_id:
        raise ValueError("no pending candidate with that id")
    if not token or token != c.token:
        raise ValueError("invalid or missing approval token")
    if not c.acceptable:
        raise ValueError(f"candidate is not acceptable ({c.banner}) — reject it instead")

    version = promote(c, baseline=baseline, versions_path=config.versions_path)
    state.active_version = version
    state.pending = None                                    # one-time: token can't be reused
    state.audit.append(AuditEntry(
        at=now_iso(), action="approve", version=version,
        detail=f"promoted {candidate_id}",
        metrics={p.param: p.proposed for p in c.changed_params},
    ))
    save_state(state, config.state_path)
    return version


def reject(config: CalibrationConfig, candidate_id: str) -> None:
    state = load_state(config.state_path)
    if state.pending is None or state.pending.id != candidate_id:
        raise ValueError("no pending candidate with that id")
    state.pending = None
    state.audit.append(AuditEntry(at=now_iso(), action="reject", version=state.active_version,
                                  detail=f"rejected {candidate_id}"))
    save_state(state, config.state_path)


def revert(config: CalibrationConfig, version: str, baseline: Path = DEFAULT_ARTIFACT) -> None:
    """Flip the active-version pointer to a prior version (must exist / validate)."""
    state = load_state(config.state_path)
    load_artifact(active_artifact_path(                       # existence/validity gate
        CalibrationState(active_version=version), baseline=baseline,
        versions_path=config.versions_path))
    prev = state.active_version
    state.active_version = version
    state.audit.append(AuditEntry(at=now_iso(), action="revert", version=version,
                                  detail=f"active {prev} -> {version}"))
    save_state(state, config.state_path)


def status(config: CalibrationConfig) -> dict[str, Any]:
    state = load_state(config.state_path)
    return {
        "active_version": state.active_version,
        "pending": (None if state.pending is None else
                    {"id": state.pending.id, "banner": state.pending.banner,
                     "acceptable": state.pending.acceptable,
                     "changed": [p.param for p in state.pending.changed_params]}),
        "audit_tail": [e.model_dump() for e in state.audit[-10:]],
    }
