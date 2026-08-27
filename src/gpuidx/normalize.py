"""Restate raw observations as the benchmark-equivalent contract.

This is where most of the index's real risk lives. A price is only evidence
about the benchmark if it is a price for the benchmark good, and almost no
observed price is. Normalisation makes them comparable; every step is
recorded so that a published value can be defended line by line.

Design rules:

* Adjustments are multiplicative and independent. Interaction effects
  certainly exist, but estimating them needs paired data nobody publishes,
  and a wrong interaction term is worse than an absent one.
* Screening beats adjusting when a mismatch cannot be honestly priced.
  Region is screened, not adjusted, for exactly this reason.
* A quote needing more than ``MAX_TOTAL_ADJUSTMENT`` in aggregate is thrown
  away. Past that point the number says more about the adjustment schedule
  than about the market.
"""

from __future__ import annotations

from .models import (
    Adjustment,
    Commitment,
    FormFactor,
    Interconnect,
    NormalizedQuote,
    QualityFlag,
    RawObservation,
)
from .spec import (
    COMMITMENT_FACTORS,
    CONTRACTS,
    FORM_FACTOR_FACTORS,
    INTERCONNECT_FACTORS,
    MAX_TOTAL_ADJUSTMENT,
    BenchmarkContract,
    node_size_factor,
    US_REGION_TOKENS,
)


class Rejection(Exception):
    """A quote that cannot be restated as the benchmark good."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def match_contract(obs: RawObservation) -> BenchmarkContract | None:
    """Map a venue's GPU string onto a benchmark contract, or None.

    Matching is exact-token based rather than fuzzy on purpose: an H100 NVL
    and an H200 are one substring apart, and a silent mismatch would corrupt
    two indices at once.
    """
    raw = obs.gpu_model.strip().lower()
    normalised = raw.replace("-", " ").replace("_", " ")
    normalised = " ".join(normalised.split())

    best: tuple[int, BenchmarkContract] | None = None
    for contract in CONTRACTS.values():
        for alias in contract.aliases:
            token = " ".join(alias.lower().replace("-", " ").replace("_", " ").split())
            if normalised == token:
                return contract
            # Prefix match guards against "H100 SXM 80GB TDP350" style suffixes
            # while refusing "H100" to match "H1000".
            if normalised.startswith(token + " "):
                score = len(token)
                if best is None or score > best[0]:
                    best = (score, contract)
    return best[1] if best else None


def _region_ok(obs: RawObservation, contract: BenchmarkContract) -> bool:
    """Screen on region. Undisclosed region is tolerated, foreign is not.

    Venues that publish a single global rate card do not attribute a region
    to a price. Discarding them would drop most of the rate-card tier, so
    they are admitted; a *disclosed* non-US region is a known mismatch and is
    discarded, because power and tax regimes are not a scalar.
    """
    if contract.region != "US":
        return True
    if obs.region is None:
        return True
    blob = obs.region.replace("_", " ").replace("-", " ").replace("/", " ").lower()
    return any(token in blob for token in US_REGION_TOKENS)


def normalize(obs: RawObservation) -> NormalizedQuote:
    """Restate one observation, or raise ``Rejection``."""
    contract = match_contract(obs)
    if contract is None:
        raise Rejection("unmatched_model", f"no contract for {obs.gpu_model!r}")

    if not _region_ok(obs, contract):
        raise Rejection("region_mismatch", f"{obs.region!r} outside {contract.region}")

    price = obs.usd_per_gpu_hour
    if price <= 0:
        raise Rejection("nonpositive_price", f"{price}")

    adjustments: list[Adjustment] = []

    ff = obs.form_factor if obs.form_factor != FormFactor.UNKNOWN else FormFactor.UNKNOWN
    factor = FORM_FACTOR_FACTORS[ff]
    if factor != 1.0:
        adjustments.append(
            Adjustment(
                name="form_factor",
                factor=factor,
                rationale=f"observed {ff.value} restated to {contract.form_factor.value}",
            )
        )

    ic = obs.interconnect
    # NVLink and InfiniBand are both benchmark-conforming fabrics; only a
    # degraded fabric attracts an adjustment.
    if ic not in (Interconnect.NVLINK, Interconnect.INFINIBAND):
        factor = INTERCONNECT_FACTORS[ic]
        if factor != 1.0:
            adjustments.append(
                Adjustment(
                    name="interconnect",
                    factor=factor,
                    rationale=f"observed {ic.value} restated to {contract.interconnect.value}",
                )
            )

    if obs.commitment != Commitment.ON_DEMAND:
        factor = COMMITMENT_FACTORS[obs.commitment]
        adjustments.append(
            Adjustment(
                name="commitment",
                factor=factor,
                rationale=f"observed {obs.commitment.value} restated to on-demand",
            )
        )

    factor = node_size_factor(obs.gpu_count, contract.node_size)
    if factor != 1.0:
        adjustments.append(
            Adjustment(
                name="node_size",
                factor=factor,
                rationale=(
                    f"{obs.gpu_count}-GPU offer restated to a "
                    f"{contract.node_size}-GPU node"
                ),
            )
        )

    total = 1.0
    for adj in adjustments:
        total *= adj.factor

    if total > MAX_TOTAL_ADJUSTMENT:
        raise Rejection(
            "over_adjusted",
            f"cumulative factor {total:.3f} exceeds cap {MAX_TOTAL_ADJUSTMENT}",
        )

    return NormalizedQuote(
        index_code=contract.index_code,
        source=obs.source,
        source_sku=obs.source_sku,
        raw_usd_per_gpu_hour=price,
        normalized_usd_per_gpu_hour=price * total,
        adjustments=adjustments,
        tier=obs.tier,
        region=obs.region,
        observed_at=obs.observed_at,
        fingerprint=obs.fingerprint(),
    )


def normalize_all(
    observations: list[RawObservation],
) -> tuple[list[NormalizedQuote], list[QualityFlag]]:
    """Normalise a batch, summarising rejections rather than listing each one."""
    quotes: list[NormalizedQuote] = []
    rejections: dict[str, int] = {}

    for obs in observations:
        try:
            quotes.append(normalize(obs))
        except Rejection as rej:
            rejections[rej.code] = rejections.get(rej.code, 0) + 1

    flags = [
        QualityFlag(
            severity="info" if code in {"unmatched_model", "region_mismatch"} else "warn",
            code=f"rejected_{code}",
            detail=f"{count} observations rejected",
        )
        for code, count in sorted(rejections.items())
    ]
    return quotes, flags
