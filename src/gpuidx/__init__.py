"""gpuidx - a reference implementation of a daily GPU rental price benchmark.

The package is organised around the lifecycle of a benchmark value:

    collect -> normalize -> screen -> estimate -> gate -> publish -> revise

Each stage is a separate module so that the methodology can be audited
independently of the plumbing that feeds it.
"""

__version__ = "0.1.0"
METHODOLOGY_VERSION = "1.0.0"
