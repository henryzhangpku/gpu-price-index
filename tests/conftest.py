from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gpuidx.models import Commitment, FormFactor, Interconnect, RawObservation, Tier


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_obs(now):
    """Build a benchmark-conforming H100 observation with targeted overrides."""

    def _make(
        source: str = "venue-a",
        price_per_gpu: float = 3.00,
        gpu_model: str = "H100 SXM",
        gpu_count: int = 8,
        commitment: Commitment = Commitment.ON_DEMAND,
        form_factor: FormFactor = FormFactor.SXM,
        interconnect: Interconnect = Interconnect.NVLINK,
        region: str | None = "us-east-1",
        tier: Tier = Tier.LIST_PRICE,
        sku: str | None = None,
    ) -> RawObservation:
        return RawObservation(
            source=source,
            source_sku=sku or f"{source}-{gpu_model}-{price_per_gpu}",
            gpu_model=gpu_model,
            gpu_count=gpu_count,
            usd_per_hour_total=price_per_gpu * gpu_count,
            commitment=commitment,
            form_factor=form_factor,
            interconnect=interconnect,
            region=region,
            tier=tier,
            observed_at=now,
        )

    return _make
