"""Signature-based calibration pipeline.

Re-fit the weakly-grounded subsurface hydrology parameters from hydrological SIGNATURES
(recession -> AGWRC, BFI -> percolation, dry-equilibrium -> leakage), grade each proposal by
data sufficiency, and email a summary for HUMAN APPROVAL before promoting to a versioned,
revertible model. Nothing auto-applies.
"""
