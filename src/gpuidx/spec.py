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


def node_size_factor(observed_gpus: int, benchmark_gpus: int) -> float:
    """Price a non-conforming node size back to the benchmark node size.

    Single and fractional-GPU rentals carry a convenience premium over a full
    node; the adjustment removes it. The curve is deliberately flat and
    bounded rather than fitted, because the underlying data is too thin to
    support a fitted shape.
    """
    if observed_gpus >= benchmark_gpus:
        return 1.00
    if observed_gpus >= 4:
        return 0.98
    if observed_gpus >= 2:
        return 0.95
    return 0.92


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


DEFAULT_GATES = Gates()
