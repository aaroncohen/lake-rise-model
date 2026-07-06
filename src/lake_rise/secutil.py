"""Security helpers shared across the serving/auth layers (stdlib-only, no cycles)."""

from __future__ import annotations

import secrets


def token_matches(provided: str | None, expected: str | None) -> bool:
    """Constant-time check that ``provided`` equals ``expected``, to deny an attacker the
    timing side-channel a short-circuiting ``==``/``!=`` would leak on a shared-secret compare.

    Returns False if either side is missing/empty: an unconfigured ``expected`` never matches,
    and an absent header never matches. Callers that must distinguish "secret not configured"
    from "wrong secret" (e.g. a 403 "disabled" vs "invalid") should gate on ``expected`` being
    set *before* calling this. ``secrets.compare_digest`` still leaks length, which is fine for
    these tokens; guard the empty cases here since it rejects a ``None`` operand outright."""
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided, expected)
