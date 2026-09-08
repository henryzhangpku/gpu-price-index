"""Measure the adjustment factors against observed venue pricing.

METHODOLOGY section 4 asserts a schedule of multiplicative factors and admits
they are calibrated judgement rather than estimated spreads. This module is
the attempt to do better, and its main result is a negative one.

Where a venue sells the *same hardware* under two commitment types, the ratio
between them is a direct observation of what that venue charges for the
difference. Pooling those ratios across venues gives an empirical estimate of
a factor. But only if the ratio is actually a price.

The distinction this module draws:

*market-determined*
    The ratio varies across SKUs, because supply and demand differ per model.
    RunPod's community-to-secure ratio ranges from 0.31 to 7.58 with a median
    near 1.32 -- that dispersion is what a real spread looks like.

*administered*
    The ratio is identical on every SKU, because the venue applies a fixed
    policy. DataCrunch prices spot at exactly half of on-demand on all 55 of
    its instance types. That number carries no information about the value of
    preemption risk; it is a discount schedule, and treating it as market
    evidence would be circular.

An administered price is also a duplicate input: it is a deterministic
function of a price already in the sample, so admitting it double-counts one
venue's view while adding noise through an uncalibrated adjustment.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .models import Commitment, QualityFlag, RawObservation

#: A ratio whose coefficient of variation across SKUs falls below this is a
#: policy, not a price. Real spreads are far noisier than 1%.
ADMINISTERED_CV = 0.01

#: Below this many pairs the CV is not meaningful and no call is made.
MIN_PAIRS = 5


@dataclass
class FactorEvidence:
    source: str
    commitment: Commitment
    pairs: int
    median_ratio: float
    cv: float
    administered: bool
    ratios: list[float] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.source}:{self.commitment.value}"


def _group_key(obs: RawObservation) -> tuple[str, str, int]:
    """Same venue, same GPU model, same node size."""
    return (obs.source, obs.gpu_model.strip().lower(), obs.gpu_count)


def observed_ratios(observations: list[RawObservation]) -> list[FactorEvidence]:
    """Estimate each venue's on-demand premium over its other commitment types."""
    groups: dict[tuple[str, str, int], dict[Commitment, float]] = {}
    for obs in observations:
        key = _group_key(obs)
        bucket = groups.setdefault(key, {})
        # If a venue lists several rows for one commitment on one model, keep
        # the cheapest -- that is the rate a buyer would actually pay.
        price = obs.usd_per_gpu_hour
        if obs.commitment not in bucket or price < bucket[obs.commitment]:
            bucket[obs.commitment] = price

    collected: dict[tuple[str, Commitment], list[float]] = {}
    for (source, _model, _n), prices in groups.items():
        base = prices.get(Commitment.ON_DEMAND)
        if not base or base <= 0:
            continue
        for commitment, price in prices.items():
            if commitment is Commitment.ON_DEMAND or price <= 0:
                continue
            collected.setdefault((source, commitment), []).append(base / price)

    evidence: list[FactorEvidence] = []
    for (source, commitment), ratios in sorted(collected.items(), key=lambda kv: kv[0][0]):
        median = statistics.median(ratios)
        cv = (statistics.pstdev(ratios) / median) if median > 0 and len(ratios) > 1 else 0.0
        evidence.append(
            FactorEvidence(
                source=source,
                commitment=commitment,
                pairs=len(ratios),
                median_ratio=median,
                cv=cv,
                administered=len(ratios) >= MIN_PAIRS and cv < ADMINISTERED_CV,
                ratios=sorted(ratios),
            )
        )
    return evidence


def administered_pairs(evidence: list[FactorEvidence]) -> set[tuple[str, Commitment]]:
    return {(e.source, e.commitment) for e in evidence if e.administered}


def drop_administered(
    observations: list[RawObservation],
) -> tuple[list[RawObservation], list[QualityFlag]]:
    """Remove quotes whose price is a fixed function of another quote.

    Returns the surviving observations and a flag per excluded venue-tier, so
    the exclusion appears on the record rather than happening silently.
    """
    evidence = observed_ratios(observations)
    excluded = administered_pairs(evidence)
    if not excluded:
        return observations, []

    kept = [o for o in observations if (o.source, o.commitment) not in excluded]

    flags: list[QualityFlag] = []
    by_pair = {(e.source, e.commitment): e for e in evidence}
    for source, commitment in sorted(excluded, key=lambda p: (p[0], p[1].value)):
        item = by_pair[(source, commitment)]
        removed = len(observations) - len(
            [o for o in observations if not (o.source == source and o.commitment == commitment)]
        )
        flags.append(
            QualityFlag(
                severity="info",
                code="administered_pricing_excluded",
                detail=(
                    f"{source} prices {commitment.value} at a constant "
                    f"{1 / item.median_ratio:.0%} of on-demand across {item.pairs} SKUs "
                    f"(CV {item.cv:.4f}); a policy rather than a price, so its "
                    f"{removed} quotes carry no independent information"
                ),
            )
        )
    return kept, flags


def compare_to_schedule(evidence: list[FactorEvidence]) -> list[dict]:
    """Line up every observed ratio against the factor the methodology asserts."""
    from .spec import COMMITMENT_FACTORS

    rows = []
    for item in evidence:
        asserted = COMMITMENT_FACTORS.get(item.commitment)
        rows.append(
            {
                "source": item.source,
                "commitment": item.commitment.value,
                "pairs": item.pairs,
                "observed": item.median_ratio,
                "asserted": asserted,
                "error": (item.median_ratio - asserted) / asserted if asserted else None,
                "cv": item.cv,
                "administered": item.administered,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Form factor
# ---------------------------------------------------------------------------
#
# METHODOLOGY section 4 said the form-factor factor had "no observable check at
# all". That was wrong, and only wrong because nobody looked. The same argument
# that calibrates the commitment factor applies here: where one venue sells the
# same GPU as PCIe and as SXM, at the same commitment and the same node size,
# the ratio between those two prices is that venue telling you directly what it
# charges for the difference.
#
# This is reported, not enforced. It measures a factor rather than screening an
# input, and moving a factor on this evidence would be premature -- the sample
# is small, several pairs come from one venue, and at least one venue quotes a
# price that cannot be real.


@dataclass
class FormFactorEvidence:
    """One venue's observed PCIe-to-SXM ratio for one benchmark contract."""

    source: str
    index_code: str
    pairs: int
    median_ratio: float
    ratios: list[float] = field(default_factory=list)

    @property
    def implied_factor(self) -> float:
        """The markup that would restate PCIe onto SXM, as this venue prices it."""
        return 1.0 / self.median_ratio if self.median_ratio > 0 else float("inf")


def form_factor_ratios(observations: list[RawObservation]) -> list[FormFactorEvidence]:
    """Observed PCIe/SXM price ratios, per venue and benchmark contract.

    Compared only within a venue, a contract, a commitment type and a node size,
    so the ratio isolates form factor rather than picking up any other
    difference between the two rows.
    """
    from .models import FormFactor
    from .normalize import match_contract

    groups: dict[tuple[str, str, str, int], dict[str, float]] = {}
    for obs in observations:
        if obs.form_factor not in (FormFactor.PCIE, FormFactor.SXM):
            continue
        contract = match_contract(obs)
        if contract is None:
            continue
        price = obs.usd_per_gpu_hour
        if price <= 0:
            continue
        key = (obs.source, contract.index_code, obs.commitment.value, obs.gpu_count)
        bucket = groups.setdefault(key, {})
        form = obs.form_factor.value
        if form not in bucket or price < bucket[form]:
            bucket[form] = price

    collected: dict[tuple[str, str], list[float]] = {}
    for (source, index_code, _commitment, _n), prices in groups.items():
        pcie, sxm = prices.get("pcie"), prices.get("sxm")
        if pcie and sxm and sxm > 0:
            collected.setdefault((source, index_code), []).append(pcie / sxm)

    evidence = []
    for (source, index_code), ratios in sorted(collected.items()):
        evidence.append(
            FormFactorEvidence(
                source=source,
                index_code=index_code,
                pairs=len(ratios),
                median_ratio=statistics.median(ratios),
                ratios=sorted(ratios),
            )
        )
    return evidence


def form_factor_summary(evidence: list[FormFactorEvidence]) -> dict:
    """Pool the per-venue medians into one estimate, honestly denominated.

    The pooling is over *venues* rather than over pairs. Six ratios from one
    venue are one venue's opinion, not six observations -- the same reason the
    estimator collapses a provider's SKUs to a single median before weighting.
    """
    from .models import FormFactor
    from .spec import FORM_FACTOR_FACTORS

    if not evidence:
        return {"venues": 0, "pairs": 0, "observed": None, "asserted": None, "error": None}

    per_venue: dict[str, list[float]] = {}
    for item in evidence:
        per_venue.setdefault(item.source, []).append(item.median_ratio)

    venue_medians = [statistics.median(v) for v in per_venue.values()]
    pooled = statistics.median(venue_medians)
    asserted = FORM_FACTOR_FACTORS[FormFactor.PCIE]
    implied = 1.0 / pooled if pooled > 0 else float("inf")

    return {
        "venues": len(per_venue),
        "pairs": sum(i.pairs for i in evidence),
        "observed_ratio": pooled,
        "implied_factor": implied,
        "asserted": asserted,
        "error": (implied - asserted) / asserted,
        "spread": (min(venue_medians), max(venue_medians)),
    }
