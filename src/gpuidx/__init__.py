"""gpuidx - a reference implementation of a daily GPU rental price benchmark.

The package is organised around the lifecycle of a benchmark value:

    collect -> normalize -> screen -> estimate -> gate -> publish -> revise

Each stage is a separate module so that the methodology can be audited
independently of the plumbing that feeds it.
"""

__version__ = "0.1.0"


def __getattr__(name: str):
    # The methodology version is a property of the registered CURRENT record
    # in spec.py, not a second constant that could drift from it. Resolved
    # lazily so importing the package does not import the whole spec.
    if name == "METHODOLOGY_VERSION":
        from .spec import CURRENT_METHODOLOGY

        return CURRENT_METHODOLOGY.version
    raise AttributeError(name)
