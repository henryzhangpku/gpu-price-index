"""Benchmark contract definitions and the adjustment schedule.

Everything in this module is a *methodology* decision rather than an
implementation detail. It is kept in one file, versioned, and changed only
through the process described in METHODOLOGY.md, because these constants are
what a settlement dispute would actually be argued over.
"""

from __future__ import annotations

from pydantic import BaseModel

from .models import Commitment, FormFactor, Interconnect


class BenchmarkContract(BaseModel):
    """The single, standardised unit an index is expressed in.

    Real venues sell wildly heterogeneous goods. The index is only meaningful
    if every input is restated as the same good; this class is that good.
    """

    index_code: str
    display_name: str
    gpu_model: str
    #: Venue-specific strings that denote this GPU model.
    aliases: list[str]
    form_factor: FormFactor
    vram_gb: int
    node_size: int
    interconnect: Interconnect
    commitment: Commitment
    region: str
    unit: str = "USD per GPU-hour"
    inclusions: str = (
        "Compute and host CPU/RAM only. Excludes persistent storage, egress, "
        "support tiers, and any committed-use or credit discount."
    )

    def describe(self) -> str:
        return (
            f"1x {self.gpu_model} {self.form_factor.value.upper()} {self.vram_gb}GB, "
            f"{self.commitment.value.replace('_', '-')}, deployed in a "
            f"{self.node_size}-GPU node with {self.interconnect.value} interconnect, "
            f"{self.region} region, dedicated (non-preemptible), "
            f"priced in {self.unit}. {self.inclusions}"
        )


CONTRACTS: dict[str, BenchmarkContract] = {
    "GIX-H100": BenchmarkContract(
        index_code="GIX-H100",
        display_name="H100 SXM Rental Price Index",
        gpu_model="NVIDIA H100",
        aliases=[
            "H100", "H100 SXM", "H100 SXM5", "H100_SXM5", "H100 80GB",
            "NVIDIA H100 80GB HBM3", "H100 PCIE", "H100_nvl", "H100 NVL",
            "NVIDIA H100 PCIe", "H100_PCIE_80G",
        ],
        form_factor=FormFactor.SXM,
        vram_gb=80,
        node_size=8,
        interconnect=Interconnect.NVLINK,
        commitment=Commitment.ON_DEMAND,
        region="US",
    ),
    "GIX-H200": BenchmarkContract(
        index_code="GIX-H200",
        display_name="H200 SXM Rental Price Index",
        gpu_model="NVIDIA H200",
        aliases=["H200", "H200 SXM", "H200_sxm5", "NVIDIA H200", "H200 NVL"],
        form_factor=FormFactor.SXM,
        vram_gb=141,
        node_size=8,
        interconnect=Interconnect.NVLINK,
        commitment=Commitment.ON_DEMAND,
        region="US",
    ),
    "GIX-A100": BenchmarkContract(
        index_code="GIX-A100",
        display_name="A100 SXM 80GB Rental Price Index",
        gpu_model="NVIDIA A100",
        aliases=[
            "A100", "A100_80G", "A100 SXM", "A100 SXM4", "A100-SXM4-80GB",
            "NVIDIA A100-SXM4-80GB", "NVIDIA A100 80GB PCIe", "A100 PCIe",
            "A100_PCIE_80G", "A100X",
        ],
        form_factor=FormFactor.SXM,
        vram_gb=80,
        node_size=8,
        interconnect=Interconnect.NVLINK,
        commitment=Commitment.ON_DEMAND,
        region="US",
    ),
    "GIX-B200": BenchmarkContract(
        index_code="GIX-B200",
        display_name="B200 SXM Rental Price Index",
        gpu_model="NVIDIA B200",
        aliases=["B200", "B200 SXM", "NVIDIA B200", "B200_sxm6", "B200 SXM6"],
        form_factor=FormFactor.SXM,
        vram_gb=180,
        node_size=8,
        interconnect=Interconnect.NVLINK,
        commitment=Commitment.ON_DEMAND,
        region="US",
    ),
    "GIX-MI300X": BenchmarkContract(
        index_code="GIX-MI300X",
        display_name="MI300X Rental Price Index",
        gpu_model="AMD MI300X",
        aliases=["MI300X", "AMD Instinct MI300X OAM", "MI300X OAM", "MI300"],
        form_factor=FormFactor.SXM,
        vram_gb=192,
        node_size=8,
        interconnect=Interconnect.INFINIBAND,
        commitment=Commitment.ON_DEMAND,
        region="US",
    ),
}


# ---------------------------------------------------------------------------
# Adjustment schedule
# ---------------------------------------------------------------------------
#
# Each factor restates a non-conforming attribute as the benchmark attribute.
# A factor above 1.0 means the observed good is CHEAPER than the benchmark
# good and must be marked up before it can be compared.
#
# These are the weakest link in any assessed benchmark: they are calibrated
# judgement, not observed spreads, because the paired trades that would let
# you estimate them directly are private. They are stated explicitly, bounded,
# and capped in aggregate precisely so that a disputing counterparty can argue
# with a number rather than with a black box. See METHODOLOGY.md section 4.

FORM_FACTOR_FACTORS: dict[FormFactor, float] = {
    FormFactor.SXM: 1.00,
    # PCIe cards run lower memory bandwidth and no NVLink fabric, and clear at
    # a persistent discount to SXM for the same die.
    FormFactor.PCIE: 1.18,
    FormFactor.UNKNOWN: 1.09,  # midpoint; also raises an uncertainty flag
}

INTERCONNECT_FACTORS: dict[Interconnect, float] = {
    Interconnect.NVLINK: 1.00,
    Interconnect.INFINIBAND: 1.00,
    # Ethernet-only nodes cannot serve multi-node training and trade lower.
    Interconnect.ETHERNET: 1.12,
    Interconnect.NONE: 1.15,
    Interconnect.UNKNOWN: 1.06,
}

COMMITMENT_FACTORS: dict[Commitment, float] = {
    Commitment.ON_DEMAND: 1.00,
    # Preemptible capacity carries interruption risk the benchmark good lacks.
    Commitment.SPOT: 1.45,
    # Peer-supplied capacity with no uptime guarantee and unvetted hosts.
    Commitment.COMMUNITY: 1.30,
    # Term commitments embed a duration discount the benchmark good lacks.
    Commitment.RESERVED: 1.25,
}


#: Breakpoints for the node-size adjustment, largest first: an offer of at
#: least ``min_gpus`` GPUs but short of the benchmark node attracts ``factor``.
NODE_SIZE_CURVE: tuple[tuple[int, float], ...] = ((4, 0.98), (2, 0.95), (1, 0.92))


def node_size_factor(
    observed_gpus: int,
    benchmark_gpus: int,
    curve: tuple[tuple[int, float], ...] = NODE_SIZE_CURVE,
) -> float:
    """Price a non-conforming node size back to the benchmark node size.

    Single and fractional-GPU rentals carry a convenience premium over a full
    node; the adjustment removes it. The curve is deliberately flat and
    bounded rather than fitted, because the underlying data is too thin to
    support a fitted shape.
    """
    if observed_gpus >= benchmark_gpus:
        return 1.00
    for min_gpus, factor in curve:
        if observed_gpus >= min_gpus:
            return factor
    return curve[-1][1] if curve else 1.00


# ---------------------------------------------------------------------------
# Product identity
# ---------------------------------------------------------------------------
#
# An alias match says the venue's string names this GPU. It does not say the
# venue is selling this GPU: the same die ships in variants that are different
# products at different prices, and a listing whose own label or stated specs
# identify one of those is not evidence about the benchmark good.
#
# Two screens, both citing the observation that motivated them:
#
# * A disclosed per-GPU VRAM more than ``VRAM_TOLERANCE`` from the contract's
#   rejects the listing. Venues disclose VRAM as a per-GPU figure or as the
#   node total; a total is recognised as such when it is a whole multiple of
#   the GPU count and implausible for one card.
# * A per-contract label rule set, for variants a venue names but does not
#   spec, so the rejection carries the reason rather than "unmatched".

#: Per-GPU VRAM disagreeing with the contract by more than this is a different
#: product. Wide enough for B200 listed as 192 GB against a 180 GB contract
#: (the raw HBM3e figure against the usable one), narrow enough to refuse an
#: A100 40 GB against the 80 GB contract.
VRAM_TOLERANCE = 0.10

class IdentityRule(BaseModel):
    """A label pattern that identifies a different product under a matched name."""

    model_config = {"frozen": True}

    #: Lower-cased token that must appear in the venue's model string, on a
    #: word boundary after the usual separators are collapsed.
    token: str
    #: What the token identifies, and why it is not the benchmark good.
    reason: str
    #: The observation that motivated the rule -- source, date, and figure.
    cited: str


PRODUCT_IDENTITY_RULES: dict[str, tuple[IdentityRule, ...]] = {
    "GIX-H100": (
        IdentityRule(
            token="nvl",
            reason=(
                "H100 NVL is the 94 GB dual-slot PCIe product bridged in pairs, "
                "not the 80 GB SXM good; it prices as a different card"
            ),
            cited="shadeform H100_nvl at 94 GB, 3 listings, 2026-09-16 snapshot",
        ),
    ),
    "GIX-A100": (
        IdentityRule(
            token="40gb",
            reason="A100 40 GB is a distinct SKU that clears well below the 80 GB card",
            cited=(
                "shadeform 'A100' at 40 GB, 17 listings, and datacrunch 'A100 SXM4 "
                "40GB', 2026-09-16 snapshot -- all of them priced into the 80 GB index"
            ),
        ),
    ),
}

#: Feeds that are a book of many sellers' asks rather than one seller's rate
#: card. Their single vote is a median across the book, and the population
#: floors in ``Gates`` say how many distinct sellers that median must span.
BOOK_SOURCES: frozenset[str] = frozenset({"vastai"})

#: Bare model strings that name a family rather than a product, and whose
#: VRAM the venue did not disclose either. Listed, not screened: the alias
#: match stands, and this is the record of the ambiguity it carries.
AMBIGUOUS_LABELS = ("A100", "H100", "MI300")


# Region is carried for screening rather than adjustment: cross-border price
# differences reflect power, tax, and latency regimes that a single scalar
# cannot honestly collapse. Non-benchmark regions are screened out instead.
US_REGION_TOKENS = (
    "us", "usa", "united states", "america", "virginia", "texas", "iowa",
    "kansas", "utah", "oregon", "california", "arizona", "georgia", "ohio",
    "nevada", "washington", "illinois", "colorado", "carolina", "york",
    "chicago", "dallas", "denver", "atlanta", "phoenix", "seattle", "ashburn",
    "santa clara", "los angeles", "san jose", "des moines", "kansas city",
    "north carolina", "new york", "salt lake",
)

#: An input requiring more than this much cumulative adjustment is too far
#: from the benchmark good to be evidence about it, and is discarded.
MAX_TOTAL_ADJUSTMENT = 1.75

#: Weight applied to each waterfall tier when forming the weighted estimate.
TIER_WEIGHTS: dict[int, float] = {1: 1.00, 2: 0.60, 3: 0.25}


# ---------------------------------------------------------------------------
# Publication gates
# ---------------------------------------------------------------------------


class Gates(BaseModel):
    """Conditions all of which must hold before a value may be published."""

    model_config = {"frozen": True}

    min_providers: int = 4
    min_observations: int = 8
    #: At least one input must be an executable offer, not a rate card.
    require_tier1: bool = False
    #: Robust coefficient of variation ceiling. A market this dispersed is not
    #: one market, and a central estimate would misrepresent it.
    max_dispersion: float = 0.45
    #: Day-over-day move beyond this is published but flagged for review.
    review_move_threshold: float = 0.15
    #: No single provider may drive more than this share of total weight.
    max_provider_weight_share: float = 0.35
    #: Minimum distinct machines a marketplace book must have recorded before
    #: that venue is allowed to price at all. Zero disables the floor.
    min_book_machines: int = 0
    #: Minimum distinct hosts behind those machines. One host offering forty
    #: boxes is one seller, however many rows it produces.
    min_book_hosts: int = 0


class EstimatorParams(BaseModel):
    """Where the estimator sits on the robustness/responsiveness trade-off.

    This used to be a position rather than a parameter: the index was the
    weighted mean of the per-provider medians, and the standard objection --
    that a mean is dragged by whatever sits at the edge of the panel -- had to
    be argued against rather than dialled.

    ``robustness_band`` is that dial. Every contributor casts three votes, at
    ``p - sigma``, ``p`` and ``p + sigma``, each carrying a third of its
    weight; the index is the weighted mean of the votes lying in the central
    ``robustness_band`` of cumulative vote mass. At 1.0 every vote counts and
    the result is exactly the weighted mean. As the band closes it becomes the
    weighted median. Neither end is privileged and the chosen value is
    published with the number.
    """

    model_config = {"frozen": True}

    #: Central share of vote mass the index is taken over. 1.0 == weighted
    #: mean; approaching 0 == weighted median.
    robustness_band: float = 1.0
    #: Minimum vote spread, as a fraction of the contributor own price.
    #:
    #: A contributor whose quotes all agree to the cent would otherwise cast
    #: three identical votes and claim a certainty it has not demonstrated --
    #: which is the normal state of a rate card that has not moved in weeks.
    #: The floor says: no contributor is more certain than this.
    sigma_floor: float = 0.03
    #: Ceiling on the spread, same units. Past this the contributor is not
    #: quoting one price for one good, and an unbounded spread would let it
    #: vote at both ends of the panel at once.
    sigma_ceiling: float = 0.50


class Screens(BaseModel):
    """Pre-normalisation screens, each individually switchable by version.

    Kept separate from ``Gates`` because the two answer different questions.
    A gate asks "may this value be published?"; a screen asks "is this
    observation evidence about the benchmark good at all?".
    """

    model_config = {"frozen": True}

    #: Drop "from $X" teaser rates. Such a price is the floor of an unstated
    #: configuration menu, not a rate for any particular configuration.
    exclude_from_floor: bool = False
    #: Drop listings whose own label or stated specs identify a different
    #: product from the one the model string claims.
    product_identity: bool = False
    #: Refuse an observation that does not say what currency it is in, rather
    #: than assuming USD.
    require_quoted_currency: bool = False
    #: A large single-contributor move with no corroboration from the rest of
    #: the panel is flagged as a probable glitch rather than a repricing.
    jump_corroboration: bool = False


class Methodology(BaseModel):
    """Everything a published value depends on, other than its inputs.

    THE POINT OF THIS CLASS. Before it existed, ``methodology_version`` was a
    string stamped onto each row and compared against one constant, so the
    first real methodology change would have made every historical value
    unverifiable at once -- ``gpuidx verify`` reports version drift as a
    failure, and the daily workflow will not commit a fixing that fails it.
    The policy in METHODOLOGY.md section 9 says a series is split at a
    methodology change rather than spliced across one; the code could say the
    version changed but could no longer reproduce either side of the split.

    A version is therefore a *behaviour*, not a label. Each one is registered
    below with the exact parameters in force under it, and ``verify``
    recomputes every tape row under the version it was published beneath. A
    value published in September 2026 still reproduces in 2030 from a build
    whose defaults have moved several times, and drift now means the only
    thing it should ever have meant: this build does not know how to reproduce
    that version.
    """

    model_config = {"frozen": True}

    version: str
    gates: Gates = Gates()
    estimator: EstimatorParams = EstimatorParams()
    screens: Screens = Screens()

    # -- the adjustment schedule, versioned with everything else -----------
    form_factor_factors: dict[FormFactor, float] = FORM_FACTOR_FACTORS
    interconnect_factors: dict[Interconnect, float] = INTERCONNECT_FACTORS
    commitment_factors: dict[Commitment, float] = COMMITMENT_FACTORS
    node_size_curve: tuple[tuple[int, float], ...] = NODE_SIZE_CURVE
    max_total_adjustment: float = MAX_TOTAL_ADJUSTMENT
    tier_weights: dict[int, float] = TIER_WEIGHTS


#: Every methodology this build can reproduce, keyed by published version.
#:
#: Entries are append-only and must never be edited once a value has been
#: published under them. Editing one silently rewrites history: the tape would
#: still name the version, and the recomputation would no longer match it.
METHODOLOGIES: dict[str, Methodology] = {
    # The launch methodology: weighted mean of per-provider medians, no
    # pre-normalisation screens beyond region and the adjustment cap.
    "1.0.0": Methodology(version="1.0.0"),
    # 2026-09-16. The screens a broad panel turned out to need, and the
    # estimator's position made a parameter. See METHODOLOGY §6 and FINDINGS
    # #12 for the numbers behind each choice, in particular why the
    # robustness band stays at 1.0.
    "1.1.0": Methodology(
        version="1.1.0",
        gates=Gates(min_book_machines=4, min_book_hosts=3),
        estimator=EstimatorParams(robustness_band=1.0, sigma_floor=0.03, sigma_ceiling=0.50),
        screens=Screens(
            exclude_from_floor=True,
            product_identity=True,
            require_quoted_currency=True,
            jump_corroboration=True,
        ),
    ),
}

#: The methodology new fixings are published under.
CURRENT_METHODOLOGY = METHODOLOGIES["1.1.0"]

#: Retained because most call sites only care about the gates. Always the
#: current methodology gates -- never construct ``Gates()`` directly for
#: anything that will be published.
DEFAULT_GATES = CURRENT_METHODOLOGY.gates


def methodology_for(version: str) -> Methodology | None:
    """The behaviour a given published version denotes, or None if unknown."""
    return METHODOLOGIES.get(version)
