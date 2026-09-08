"""The explanatory commands must agree with the estimator they narrate.

``screen`` and ``weights`` exist to be read aloud: they print the arithmetic
behind one fixing so that a reader can follow the decision rather than trust
it. That makes their failure mode unusual. A wrong number here is not a wrong
index -- the index is computed elsewhere -- it is a *misleading explanation* of
a correct one, which is worse, because it is the artefact people are invited to
check the index against.

Two properties are pinned. ``weights`` must reconcile to the published value,
so the displayed weighting is the weighting that was actually used. ``screen``
must account for every provider, kept and screened alike, so the command that
justifies removing data cannot quietly omit what it removed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from gpuidx import cli
from gpuidx.archive import append_to_tape, write_snapshot
from gpuidx.models import Commitment, FormFactor, Interconnect, RawObservation, Tier

#: Wide enough that rich never crops a provider name out of the assertions.
WIDE = {"COLUMNS": "200", "TERM": "dumb", "PYTHONIOENCODING": "utf-8"}

PRICES = {
    "venue-a": 3.00,
    "venue-b": 3.10,
    "venue-c": 3.20,
    "venue-d": 3.30,
    "venue-e": 3.40,
    "hyperscaler": 40.00,  # far enough out to be screened on any sane threshold
}


def _obs(source: str, price: float) -> RawObservation:
    return RawObservation(
        source=source,
        source_sku=f"{source}-h100",
        gpu_model="H100 SXM",
        gpu_count=8,
        usd_per_hour_total=price * 8,
        commitment=Commitment.ON_DEMAND,
        form_factor=FormFactor.SXM,
        interconnect=Interconnect.NVLINK,
        region="us-east",
        tier=Tier.EXECUTABLE,
        observed_at=datetime.now(UTC),
    )


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """One fixing with a clear outlier, and the CLI pointed at it."""
    snapshot = write_snapshot(tmp_path, [_obs(s, p) for s, p in PRICES.items()])
    append_to_tape(
        tmp_path,
        [
            {
                "index_code": "GIX-H100",
                "index_date": "2026-09-03",
                "revision": 0,
                "status": "published",
                "value": 3.20,
                "provider_count": len(PRICES) - 1,
                "observation_count": len(PRICES),
                "dispersion": 0.05,
                "withheld_reason": "",
                "methodology_version": "1.0.0",
                "published_at": "2026-09-03T14:05:00Z",
                "superseded_at": "",
                "revision_reason": "",
                "snapshot": snapshot.name,
            }
        ],
    )
    monkeypatch.setattr(cli, "ARCHIVE_ROOT", tmp_path)
    return tmp_path


def _run(*args):
    return CliRunner().invoke(cli.app, list(args), env=WIDE)


def test_screen_accounts_for_every_provider(archive):
    """Kept plus screened must equal the panel. A screen that hides its own
    casualties is indistinguishable from silently deleting them."""
    result = _run("screen", "GIX-H100", "2026-09-03")
    assert result.exit_code == 0, result.output
    for source in PRICES:
        assert source in result.output, f"{source} missing from the screen table"


def test_screen_marks_the_outlier_and_only_the_outlier(archive):
    result = _run("screen", "GIX-H100", "2026-09-03")
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if any(s in ln for s in PRICES)]
    screened = [ln for ln in lines if "screened" in ln]
    assert len(screened) == 1, result.output
    assert "hyperscaler" in screened[0]


def test_weights_reconciles_to_the_published_value(archive):
    """The printed weighted mean is the claim; the published value is the fact."""
    result = _run("weights", "GIX-H100", "2026-09-03")
    assert result.exit_code == 0, result.output

    line = next(ln for ln in result.output.splitlines() if "sum(w x price)" in ln)
    shown = float(line.split("=")[1].split()[0])
    published = float(line.split("published")[1].strip())
    assert shown == pytest.approx(published, abs=5e-4), line


def test_weights_excludes_screened_providers(archive):
    """Screening removes weight. A provider that was screened must not appear
    with a share, or the two commands would tell different stories."""
    result = _run("weights", "GIX-H100", "2026-09-03")
    assert result.exit_code == 0, result.output
    assert "hyperscaler" not in result.output


@pytest.mark.parametrize("command", ["screen", "weights"])
def test_date_defaults_to_the_latest_fixing(archive, command):
    """Both are meant to be typed without arguments during a walkthrough."""
    bare = _run(command, "GIX-H100")
    dated = _run(command, "GIX-H100", "2026-09-03")
    assert bare.exit_code == 0, bare.output
    assert bare.output == dated.output


@pytest.mark.parametrize("command", ["screen", "weights"])
def test_unknown_fixing_exits_nonzero(archive, command):
    result = _run(command, "GIX-H100", "1999-01-01")
    assert result.exit_code != 0
