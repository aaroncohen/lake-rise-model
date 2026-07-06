"""Constant-time token comparison used to gate the alert-send, calibration-approve, and
one-time-proposal-token paths."""

from lake_rise.secutil import token_matches


def test_matches_only_on_exact_equality():
    assert token_matches("s3cret", "s3cret") is True
    assert token_matches("s3cret", "wrong") is False
    assert token_matches("s3cre", "s3cret") is False   # prefix is not a match


def test_missing_or_empty_operands_never_match():
    # An absent header or an unconfigured secret must never authorize.
    assert token_matches(None, "s3cret") is False
    assert token_matches("s3cret", None) is False
    assert token_matches("", "s3cret") is False
    assert token_matches("s3cret", "") is False
    assert token_matches(None, None) is False
