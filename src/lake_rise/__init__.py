"""Crystal Lake lake-rise prediction system.

A small deterministic HSPF-style interflow bucket model, stepped hourly. See the
plan and the design docs for the full specification. The model library
(``model``, ``geometry``, ``spillway``, ``units``) is pure and framework-free; it
is imported by both the predictor and (eventually) the calibration loop to avoid
training/serving skew.
"""

__version__ = "0.1.0"
