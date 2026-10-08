"""One small parser per provider page.

Each parser takes the archived bytes, the capture timestamp and the source
name, and returns every priced configuration it can read for the GPUs the
backfill covers. Parsers do no restatement; they map the page's wording onto
model strings the live contract aliases recognise and record the rest
(count, form factor, fabric, commitment) only where the page states it.

A parser that cannot read a page returns an empty list rather than raising;
the manifest records ``rows_parsed`` per snapshot, so a page the parser stopped
understanding shows up as a run of zeros rather than as a crash.
"""

from __future__ import annotations

PARSERS: dict = {}


def register(name: str):
    def deco(fn):
        PARSERS[name] = fn
        return fn

    return deco


from . import (  # noqa: E402,F401
    coreweave,
    crusoe,
    datacrunch,
    digitalocean,
    fluidstack,
    hyperstack,
    lambda_,
    nebius,
    paperspace,
    runpod,
    voltagepark,
)
