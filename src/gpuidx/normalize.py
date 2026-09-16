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
    PriceKind,
    QualityFlag,
    RawObservation,
)
from .spec import (
    BOOK_SOURCES,
    CONTRACTS,
    CURRENT_METHODOLOGY,
    PRODUCT_IDENTITY_RULES,
    US_REGION_TOKENS,
    VRAM_TOLERANCE,
    BenchmarkContract,
    Methodology,
    node_size_factor,
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


def _label_tokens(gpu_model: str) -> set[str]:
    return set(gpu_model.lower().replace("-", " ").replace("_", " ").split())


def vram_readings(obs: RawObservation) -> set[float]:
    """Every per-GPU VRAM the disclosed figure could honestly mean.

    Venues are inconsistent about whether the number is per card or per node,
    and nothing in the payload says which. DataCrunch lists a 2x H100 node as
    160 GB and an 8x as 640 GB; Shadeform lists the card. Both readings are
    therefore taken and a listing stands if either matches the contract. The
    ambiguity is the venue's, and a screen that guessed one convention would
    reject an honest node figure -- which the first draft of this did, holding
    out every multi-GPU DataCrunch listing for disclosing its node total.
    """
    if obs.vram_gb is None or obs.vram_gb <= 0:
        return set()
    readings = {float(obs.vram_gb)}
    if obs.gpu_count > 1:
        readings.add(obs.vram_gb / obs.gpu_count)
    return readings


def product_identity(obs: RawObservation, contract: BenchmarkContract) -> str | None:
    """Why this listing is a different product from the contract's, or None.

    An alias match says the venue's string names this GPU. This asks whether
    the venue's own label or stated specs say it is selling a *variant* of it
    -- a 40 GB A100 into an 80 GB index, an NVL card into an SXM one -- which
    the adjustment schedule cannot price and must not try to.
    """
    tokens = _label_tokens(obs.gpu_model)
    for rule in PRODUCT_IDENTITY_RULES.get(contract.index_code, ()):
        if rule.token in tokens:
            return f"{obs.gpu_model!r}: {rule.reason}"

    readings = vram_readings(obs)
    if readings and all(
        abs(r - contract.vram_gb) > VRAM_TOLERANCE * contract.vram_gb for r in readings
    ):
        per_gpu = min(readings, key=lambda r: abs(r - contract.vram_gb))
        return (
            f"{obs.gpu_model!r} discloses {obs.vram_gb} GB (read as {per_gpu:g} GB per GPU) "
            f"against the contract's {contract.vram_gb} GB; a different product under "
            "the same name"
        )
    return None


def normalize(
    obs: RawObservation, methodology: Methodology = CURRENT_METHODOLOGY
) -> NormalizedQuote:
    """Restate one observation under a methodology, or raise ``Rejection``.

    The adjustment schedule comes from ``methodology`` rather than from module
    constants so that a value published under an earlier version can be
    recomputed under that version's factors, not today's.
    """
    contract = match_contract(obs)
    if contract is None:
        raise Rejection("unmatched_model", f"no contract for {obs.gpu_model!r}")

    screens = methodology.screens

    # A "from $X" teaser is the floor of a menu the venue did not publish. It
    # says the venue is in the market; it does not say at what price.
    if screens.exclude_from_floor and obs.price_kind == PriceKind.FROM_FLOOR:
        raise Rejection("from_floor", f"{obs.source_sku!r} is a teaser floor, not a rate")

    # Record, never assume. There is no FX table here and an observation in a
    # currency the benchmark is not denominated in is held out until there is
    # one, rather than treated as dollars because the field name says so.
    if screens.require_quoted_currency and obs.currency.upper() != "USD":
        raise Rejection("currency_unsupported", f"quoted in {obs.currency}, no rate to restate it")

    if screens.product_identity:
        why = product_identity(obs, contract)
        if why is not None:
            raise Rejection("product_identity", why)

    if not _region_ok(obs, contract):
        raise Rejection("region_mismatch", f"{obs.region!r} outside {contract.region}")

    price = obs.usd_per_gpu_hour
    if price <= 0:
        raise Rejection("nonpositive_price", f"{price}")

    adjustments: list[Adjustment] = []

    ff = obs.form_factor if obs.form_factor != FormFactor.UNKNOWN else FormFactor.UNKNOWN
    factor = methodology.form_factor_factors[ff]
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
        factor = methodology.interconnect_factors[ic]
        if factor != 1.0:
            adjustments.append(
                Adjustment(
                    name="interconnect",
                    factor=factor,
                    rationale=f"observed {ic.value} restated to {contract.interconnect.value}",
                )
            )

    if obs.commitment != Commitment.ON_DEMAND:
        factor = methodology.commitment_factors[obs.commitment]
        adjustments.append(
            Adjustment(
                name="commitment",
                factor=factor,
                rationale=f"observed {obs.commitment.value} restated to on-demand",
            )
        )

    factor = node_size_factor(obs.gpu_count, contract.node_size, methodology.node_size_curve)
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

    if total > methodology.max_total_adjustment:
        raise Rejection(
            "over_adjusted",
            f"cumulative factor {total:.3f} exceeds cap {methodology.max_total_adjustment}",
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


def prepare_quotes(
    observations: list[RawObservation],
    methodology: Methodology = CURRENT_METHODOLOGY,
) -> tuple[list[NormalizedQuote], list[QualityFlag]]:
    """The single path from raw observations to index inputs.

    Both publication and verification must go through here. When they were
    two code paths, adding a filter to one silently made every published value
    irreproducible from its own archive -- which ``gpuidx verify`` caught, but
    only because the check existed. One function removes the possibility.
    """
    # Imported here rather than at module scope: calibrate imports spec, and
    # spec imports models, so a top-level import would close a cycle.
    from .calibrate import drop_administered

    informative, administered_flags = drop_administered(observations)
    populated, book_flags = hold_out_thin_books(informative, methodology)
    quotes, rejection_flags = normalize_all(populated, methodology)
    return quotes, administered_flags + book_flags + rejection_flags


def is_book(obs: RawObservation) -> bool:
    """A book is a feed of many sellers' asks, not one seller's rate card.

    Decided by the source, not by whether the row happens to carry a machine
    id. The first draft did the latter, and it had the failure this repository
    keeps finding elsewhere: an adapter that silently stopped recording ids
    would have turned the marketplace back into a rate card, exempt from the
    floor, and nothing would have said so. A book that cannot prove its
    population is held out; it does not get to stop being a book.
    """
    return obs.source in BOOK_SOURCES


def hold_out_thin_books(
    observations: list[RawObservation],
    methodology: Methodology = CURRENT_METHODOLOGY,
) -> tuple[list[RawObservation], list[QualityFlag]]:
    """Hold a marketplace's seat out of any index its book is too thin to price.

    A price list is one seller's statement and gets one vote. A marketplace
    book is many sellers' asks and gets one vote too -- but that vote is a
    median across the book, and a median of three machines from one host is
    that host's rate card wearing a marketplace's name. Below the floors the
    venue does not price the index at all; the counts are recorded so the
    hold-out is on the tape rather than silent.

    Population accounting fails closed. A book whose rows do not identify
    their machines cannot prove how many it recorded, and is held out rather
    than trusted, for the same reason a read that reports nothing is not
    evidence of nothing.
    """
    gates = methodology.gates
    if gates.min_book_machines <= 0 and gates.min_book_hosts <= 0:
        return observations, []

    # (source, index_code) -> machines, hosts, rows
    machines: dict[tuple[str, str], set] = {}
    hosts: dict[tuple[str, str], set] = {}
    rows: dict[tuple[str, str], int] = {}
    for obs in observations:
        if not is_book(obs):
            continue
        contract = match_contract(obs)
        if contract is None:
            continue
        key = (obs.source, contract.index_code)
        rows[key] = rows.get(key, 0) + 1
        m = obs.payload.get("machine_id")
        h = obs.payload.get("host_id")
        if m is not None:
            machines.setdefault(key, set()).add(m)
        if h is not None:
            hosts.setdefault(key, set()).add(h)

    held: dict[tuple[str, str], str] = {}
    for key, n in rows.items():
        m = len(machines.get(key, ()))
        h = len(hosts.get(key, ()))
        if key not in machines and key not in hosts:
            held[key] = f"none of its {n} rows identify a machine or host, so the population is unproven"
        elif gates.min_book_machines > 0 and m < gates.min_book_machines:
            held[key] = f"{m} distinct machines of {gates.min_book_machines} required across {n} rows"
        elif gates.min_book_hosts > 0 and h < gates.min_book_hosts:
            held[key] = f"{h} distinct hosts of {gates.min_book_hosts} required across {n} rows"

    if not held:
        return observations, []

    def _key(obs: RawObservation) -> tuple[str, str] | None:
        if not is_book(obs):
            return None
        contract = match_contract(obs)
        return (obs.source, contract.index_code) if contract else None

    kept = [o for o in observations if _key(o) not in held]
    flags = [
        QualityFlag(
            severity="warn",
            code="book_population_floor",
            index_code=index_code,
            detail=(
                f"{source} held out of {index_code}: {why}; a median over that few "
                "sellers is one seller's rate card wearing a marketplace's name"
            ),
        )
        for (source, index_code), why in sorted(held.items())
    ]
    return kept, flags


def normalize_all(
    observations: list[RawObservation],
    methodology: Methodology = CURRENT_METHODOLOGY,
) -> tuple[list[NormalizedQuote], list[QualityFlag]]:
    """Normalise a batch, summarising rejections rather than listing each one.

    Prefer ``prepare_quotes`` -- this is the normalisation step alone and does
    not apply the pre-normalisation screens the pipeline relies on.
    """
    quotes: list[NormalizedQuote] = []
    rejections: dict[str, int] = {}

    for obs in observations:
        try:
            quotes.append(normalize(obs, methodology))
        except Rejection as rej:
            rejections[rej.code] = rejections.get(rej.code, 0) + 1

    flags = [
        QualityFlag(
            severity="info" if code in QUIET_REJECTIONS else "warn",
            code=f"rejected_{code}",
            detail=f"{count} observations rejected",
        )
        for code, count in sorted(rejections.items())
    ]
    return quotes, flags


#: Rejections that are the normal state of a broad feed rather than a warning:
#: most of what a venue lists is not in the benchmark's universe, and a
#: variant of the right GPU is the expected shape of that.
QUIET_REJECTIONS = frozenset({"unmatched_model", "region_mismatch", "product_identity"})
