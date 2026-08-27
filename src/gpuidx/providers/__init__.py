"""Provider registry and the collection driver."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import QualityFlag, RawObservation
from .base import Provider, make_client
from .curated import Curated
from .datacrunch import DataCrunch
from .runpod import RunPod
from .shadeform import Shadeform
from .vastai import VastAI

__all__ = ["CollectionRun", "Provider", "collect_all", "default_providers"]


def default_providers() -> list[Provider]:
    return [Shadeform(), RunPod(), VastAI(), DataCrunch(), Curated()]


@dataclass
class CollectionRun:
    observations: list[RawObservation] = field(default_factory=list)
    flags: list[QualityFlag] = field(default_factory=list)
    per_provider: dict[str, int] = field(default_factory=dict)


def collect_all(providers: list[Provider] | None = None) -> CollectionRun:
    """Run every adapter, tolerating individual failures.

    A venue being unreachable must not abort the run: the publication gates
    exist precisely to decide whether what survived is still enough to
    publish. Swallowing the error here and recording it as a flag keeps that
    decision in one place.
    """
    providers = providers or default_providers()
    run = CollectionRun()

    with make_client() as client:
        for provider in providers:
            try:
                observations = provider.collect(client)
            except Exception as exc:
                run.flags.append(
                    QualityFlag(
                        severity="error",
                        code="provider_unreachable",
                        detail=f"{provider.name}: {type(exc).__name__}: {exc}",
                    )
                )
                run.per_provider[provider.name] = 0
                continue

            run.observations.extend(observations)
            run.per_provider[provider.name] = len(observations)

            if not observations:
                run.flags.append(
                    QualityFlag(
                        severity="warn",
                        code="provider_empty",
                        detail=f"{provider.name} returned no rows",
                    )
                )

            dropped = getattr(provider, "dropped_stale", None)
            if dropped:
                run.flags.append(
                    QualityFlag(
                        severity="warn",
                        code="curated_entry_stale",
                        detail=f"dropped {len(dropped)}: {', '.join(dropped)}",
                    )
                )

    return run
