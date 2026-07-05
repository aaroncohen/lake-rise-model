"""Parameter provenance/tunability registry: the guards that keep it trustworthy and
in sync with the artifact."""

import json

import pytest
from typer.testing import CliRunner

from lake_rise import registry as R
from lake_rise.artifact import DEFAULT_ARTIFACT, load_artifact
from lake_rise.cli import app

runner = CliRunner()


@pytest.fixture
def reg():
    return R.load_registry()


@pytest.fixture
def raw():
    return json.loads(DEFAULT_ARTIFACT.read_text())


# --- completeness: nothing escapes provenance labelling (the anti-rot guard) ------------

def test_every_artifact_leaf_is_covered(reg, raw):
    """Every leaf in the artifact JSON is registered (directly or via a table ancestor) or
    explicitly ignored. A newly added parameter fails this until it is classified."""
    assert R.uncovered_paths(raw, reg) == []


def test_registry_paths_resolve_and_classes_valid(reg, art):
    for path, spec in reg.parameters.items():
        assert spec.cls in R.CLASSES, f"{path} has bad class {spec.cls!r}"
        if spec.location == "artifact":
            R.get(art, path)  # must resolve on the live artifact (raises if drifted)


def test_couples_with_targets_are_registered(reg):
    for path, spec in reg.parameters.items():
        for target in spec.couples_with:
            assert target in reg.parameters, f"{path} couples_with unknown path {target!r}"


def test_tunable_values_and_priors_within_range(reg, art):
    for path, spec in reg.parameters.items():
        if not (spec.tunable and not spec.table and spec.location == "artifact"):
            continue
        value = R.get(art, path)
        assert R.in_range(spec, value), f"{path}={value} outside [{spec.min}, {spec.max}]"
        if spec.prior is not None:
            assert R.in_range(spec, spec.prior), f"{path} prior {spec.prior} outside range"


def test_the_tunable_set_is_the_expected_six(reg, art):
    tunable = {r["path"] for r in R.list_parameters(reg, art, tunable=True)}
    assert tunable == {
        "hspf.PERC_coeff", "hspf.AGWRC_per_day", "spillway.leakage.cfs_per_ft2",
        "seasonal_agw_default_in", "watershed.lag_hours", "datum.sensor_to_absolute_offset_ft",
    }


def test_auto_tunable_is_the_three_subsurface_params_and_subset_of_tunable(reg):
    auto = set(R.auto_tunable_paths(reg))
    assert auto == {"hspf.PERC_coeff", "hspf.AGWRC_per_day", "spillway.leakage.cfs_per_ft2"}
    # the calibration pipeline may only touch things a human is also allowed to tune
    for path in auto:
        assert reg.parameters[path].tunable


# --- validate-on-set: the set seam must be type-safe -----------------------------------

def test_set_scalar_coerces_and_rejects_bad_type(art):
    R.set(art, "hspf.PERC_coeff", "0.28")          # string from a CLI-like source
    assert R.get(art, "hspf.PERC_coeff") == pytest.approx(0.28)
    assert isinstance(R.get(art, "hspf.PERC_coeff"), float)
    with pytest.raises(Exception):
        R.set(art, "hspf.PERC_coeff", "not-a-number")


def test_set_dict_element_validates_through_parent(art):
    """A dict element bypasses validate_assignment; set() re-validates the parent field, so a
    list written to a tuple-typed table is coerced to a tuple rather than silently stored."""
    R.set(art, "uncertainty.lead_ratio_by_day.3", [0.4, 2.1])
    got = R.get(art, "uncertainty.lead_ratio_by_day.3")
    assert isinstance(got, tuple) and got == (0.4, 2.1)


def test_get_set_roundtrip_across_shapes(art):
    for path, val in [("watershed.lag_hours", 5.0),          # nested model scalar
                      ("monthly_pet_in.7", 4.3),             # string-keyed dict element
                      ("datum.sensor_to_absolute_offset_ft", 338.5)]:
        R.set(art, path, val)
        assert R.get(art, path) == pytest.approx(val)


# --- check_write: the range/tunability gate for the canonical write path ----------------

def test_check_write_blocks_bad_writes_and_allows_valid(reg):
    with pytest.raises(ValueError):
        R.check_write(reg, "hspf.PERC_coeff", 9.9)                 # out of range
    with pytest.raises(ValueError):
        R.check_write(reg, "spillway.weir_exponent", 2.0)          # not tunable (test-locked)
    with pytest.raises(ValueError):
        R.check_write(reg, "units.CFS_TO_ACFT_PER_HR", 0.09)       # code constant
    with pytest.raises(ValueError):
        R.check_write(reg, "seasonal_agw_default_in", 0.5)         # whole table
    with pytest.raises(ValueError):
        R.check_write(reg, "hspf.not_a_param", 1.0)                # unregistered
    R.check_write(reg, "hspf.PERC_coeff", 0.28)                    # valid -> no raise


# --- CLI ---------------------------------------------------------------------------------

def test_cli_params_lists_tunable():
    result = runner.invoke(app, ["params", "--tunable"])
    assert result.exit_code == 0
    assert "hspf.PERC_coeff" in result.stdout
    assert "TUNABLE" in result.stdout
    assert "couples_with" in result.stdout


def test_cli_params_set_writes_valid_tuned_artifact(tmp_path):
    out = tmp_path / "v1.json"
    result = runner.invoke(app, ["params", "--set", "hspf.PERC_coeff=0.28", "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    tuned = load_artifact(out)
    assert tuned.hspf.PERC_coeff == pytest.approx(0.28)
    # inline provenance comments survive the write (raw-JSON edit, not model_dump)
    assert "percolation_comment" in json.loads(out.read_text())["hspf"]


def test_cli_params_set_rejects_untunable_and_writes_nothing(tmp_path):
    out = tmp_path / "nope.json"
    result = runner.invoke(app, ["params", "--set", "spillway.weir_exponent=2", "--out", str(out)])
    assert result.exit_code == 1
    assert not out.exists()


def test_cli_params_set_requires_out(tmp_path):
    result = runner.invoke(app, ["params", "--set", "hspf.PERC_coeff=0.28"])
    assert result.exit_code == 1
