"""Parameter provenance & tunability registry.

The model's parameters live in the JSON artifact; their *provenance* has until now been
prose only (inline comments + the calibration-log Confidence column). This module adds a
machine-readable sidecar (``artifacts/parameter_registry.json``) that classifies each
parameter by provenance, marks which are tunable, records plausible ranges, a
regularization ``prior``, and which other parameters each one ``couples_with`` -- the
metadata the sensitivity sweep and the auto-calibrator will need.

The registry holds METADATA ONLY, keyed by dotted parameter path; the numeric values stay
in the artifact (single source of truth). ``get``/``set`` resolve dotted paths on the
mutable ``Artifact``; ``set`` is type-safe (the artifact models use ``validate_assignment``)
and ``check_write`` hard-fails on out-of-range / non-tunable writes so a bad value can never
become a canonical artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field

from .artifact import DEFAULT_ARTIFACT, Artifact

DEFAULT_REGISTRY = DEFAULT_ARTIFACT.parent / "parameter_registry.json"

# Provenance classes, mirroring the calibration-log "Confidence" column.
CLASSES = {"research", "gauge-calibrated", "reasoned", "provisional", "test-locked"}


class ParameterSpec(BaseModel):
    """Provenance/tunability metadata for one parameter path. Only the tunable set carries
    the full metadata (range/prior/couples_with); everything else carries just ``cls``."""
    model_config = ConfigDict(populate_by_name=True)

    cls: str = Field(alias="class")           # one of CLASSES (JSON key "class")
    tunable: bool = False                      # a human may write it via `params --set`
    auto_tunable: bool = False                 # the calibration pipeline may propose changes to it
    table: bool = False                       # True -> this path is a homogeneous dict/table
    location: str = "artifact"                # "artifact" | "code" (code consts can't be --set)
    min: float | None = None
    max: float | None = None
    units: str | None = None
    prior: float | None = None                # research/nominal value to regularize toward
    prior_strength: str | None = None         # "weak" | "medium" | "strong"
    couples_with: list[str] = []              # paths/anchors this is NOT independent of
    source: str | None = None
    log_ref: str | None = None
    notes: str | None = None


class Registry(BaseModel):
    parameters: dict[str, ParameterSpec]
    ignore: list[str] = []                    # exact paths / leaf names to skip in completeness


def load_registry(path: str | Path | None = None) -> Registry:
    p = Path(path) if path is not None else DEFAULT_REGISTRY
    data = json.loads(p.read_text())
    params = {k: ParameterSpec.model_validate(v) for k, v in data.get("parameters", {}).items()}
    return Registry(parameters=params, ignore=data.get("ignore", []))


# --- dotted-path get/set on the mutable Artifact ----------------------------------------

def _split(path: str) -> list[str]:
    return path.split(".")


def get(art: Artifact, path: str) -> Any:
    """Resolve a dotted path on the artifact: nested models, string-keyed dicts, and
    list/tuple indices (e.g. ``hspf.PERC_coeff``, ``monthly_pet_in.7``,
    ``uncertainty.lead_ratio_by_day.3``)."""
    obj: Any = art
    for part in _split(path):
        if isinstance(obj, BaseModel):
            obj = getattr(obj, part)
        elif isinstance(obj, dict):
            obj = obj[part]
        elif isinstance(obj, (list, tuple)):
            obj = obj[int(part)]
        else:
            raise KeyError(f"cannot resolve '{part}' in path '{path}' (reached {type(obj).__name__})")
    return obj


def set(art: Artifact, path: str, value: Any) -> None:
    """Set a dotted path IN PLACE, type-safely. A model attribute is set via ``setattr``,
    which ``validate_assignment`` coerces/validates. A dict/list element bypasses pydantic,
    so we rebuild the container and re-assign it to its enclosing model field -- which is
    itself validated -- so element writes are type-checked too.

    NOTE: type safety only. Range/tunability is a business rule -- gate writes with
    ``check_write`` before calling this on the canonical write path."""
    parts = _split(path)
    parent = get(art, ".".join(parts[:-1])) if len(parts) > 1 else art
    key = parts[-1]

    if isinstance(parent, BaseModel):
        setattr(parent, key, value)                       # validate_assignment handles it
        return

    # parent is a dict or sequence -> rebuild and re-assign through the enclosing model field
    if len(parts) < 2:
        raise KeyError(f"cannot set container path '{path}'")
    enclosing = get(art, ".".join(parts[:-2])) if len(parts) > 2 else art
    field = parts[-2]
    container = getattr(enclosing, field)
    if isinstance(container, dict):
        rebuilt: Any = {**container, key: value}
    elif isinstance(container, (list, tuple)):
        seq = list(container)
        seq[int(key)] = value
        rebuilt = seq
    else:
        raise KeyError(f"cannot set '{key}' in {type(container).__name__} at '{path}'")
    setattr(enclosing, field, rebuilt)                    # re-validates the whole field


# --- write gating -----------------------------------------------------------------------

def in_range(spec: ParameterSpec, value: float) -> bool:
    if spec.min is not None and value < spec.min:
        return False
    if spec.max is not None and value > spec.max:
        return False
    return True


def check_write(reg: Registry, path: str, value: Any) -> None:
    """Raise if ``path`` is not a writable, tunable, in-range target. This is the gate the
    CLI --set write path must pass before producing a new canonical artifact."""
    spec = reg.parameters.get(path)
    if spec is None:
        raise ValueError(f"'{path}' is not a registered parameter")
    if spec.location != "artifact":
        raise ValueError(f"'{path}' is a {spec.location}-level constant and cannot be written to the artifact")
    if not spec.tunable:
        raise ValueError(f"'{path}' is class '{spec.cls}' and is not marked tunable -- refusing to write")
    if spec.table:
        raise ValueError(f"'{path}' is a whole-table parameter; per-cell tuning is not supported yet")
    if isinstance(value, (int, float)) and not in_range(spec, float(value)):
        raise ValueError(
            f"'{path}'={value} is outside the plausible range [{spec.min}, {spec.max}] -- refusing to write")


# --- listing / introspection ------------------------------------------------------------

def auto_tunable_paths(reg: Registry) -> list[str]:
    """Paths the calibration pipeline is allowed to propose changes to (a subset of the
    human-tunable set). Sorted for deterministic ordering."""
    return sorted(p for p, s in reg.parameters.items() if s.auto_tunable)


def list_parameters(reg: Registry, art: Artifact, cls: str | None = None,
                    tunable: bool | None = None) -> list[dict[str, Any]]:
    """Enumerate registry entries joined with their live artifact value, optionally filtered
    by provenance class and/or tunability. Sorted by (class, path)."""
    rows: list[dict[str, Any]] = []
    for path, spec in reg.parameters.items():
        if cls is not None and spec.cls != cls:
            continue
        if tunable is not None and spec.tunable != tunable:
            continue
        try:
            value = get(art, path) if spec.location == "artifact" else None
        except Exception:  # noqa: BLE001 -- a stale registry path shouldn't crash a listing
            value = "<unresolved>"
        rows.append({"path": path, "value": value, **spec.model_dump(by_alias=True)})
    rows.sort(key=lambda r: (r["class"], r["path"]))
    return rows


# --- completeness (anti-rot guard for the test) -----------------------------------------

def iter_leaf_paths(data: Any, prefix: str = "") -> Iterator[str]:
    """Yield every leaf (scalar / list / tuple) dotted path in a raw artifact dict."""
    if isinstance(data, dict):
        for k, v in data.items():
            child = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                yield from iter_leaf_paths(v, child)
            else:
                yield child
    else:
        if prefix:
            yield prefix


def is_covered(path: str, reg: Registry) -> bool:
    """A leaf is covered if it (or a table ancestor) is registered, or it's ignored."""
    leaf = path.split(".")[-1]
    if path in reg.ignore or leaf in reg.ignore:
        return True
    if path in reg.parameters:
        return True
    # a table registry key covers all of its children
    for reg_path, spec in reg.parameters.items():
        if spec.table and path.startswith(reg_path + "."):
            return True
    return False


def uncovered_paths(art_data: dict, reg: Registry) -> list[str]:
    """Leaf paths in the raw artifact JSON that no registry entry (or ignore rule) covers."""
    return [p for p in iter_leaf_paths(art_data) if not is_covered(p, reg)]
