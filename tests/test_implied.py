"""Reading a distribution off a bracket ladder.

The arithmetic is simple; what these tests pin down is that the ladder's own
weaknesses -- a book that does not sum to one, mass sitting in open tails,
thin volume -- reach the output instead of being smoothed away, and that a
ladder too poor to quote is withheld rather than published.

The fixture is the live Polymarket H100 end-of-October ladder as at
2026-09-16, quotes included, so the numbers below are real.
"""

from __future__ import annotations

import pytest

from gpuidx.implied import (
    Bracket,
    ImpliedDistribution,
    distribution_from_event,
    expectation_sensitivity,
    parse_bracket,
)


def ladder(quotes, volume=20_000.0):
    """Build a distribution from (label, quote) pairs, volume spread evenly."""
    per = volume / max(1, len(quotes))
    brackets = []
    for label, q in quotes:
        low, high = parse_bracket(label)
        brackets.append(Bracket(label=label, low=low, high=high, quoted=q, volume=per))
    return ImpliedDistribution(
        tenor="test", settles=None, source_index="Ornn H100 Index", brackets=brackets
    )


# The real end-of-October book, 2026-09-16.
OCTOBER = [
    ("<$2.00", 0.1475),
    ("$2.00-$2.25", 0.0960),
    ("$2.25-$2.50", 0.1250),
    ("$2.50-$2.75", 0.2050),
    ("$2.75-$3.00", 0.1400),
    ("$3.00+", 0.2400),
]


class TestParseBracket:
    def test_open_below(self):
        assert parse_bracket("<$2.00") == (None, 2.00)

    def test_closed(self):
        assert parse_bracket("$2.25-$2.50") == (2.25, 2.50)

    def test_open_above(self):
        assert parse_bracket("$3.25+") == (3.25, None)
        assert parse_bracket(">$4.00") == (4.00, None)

    def test_no_number_is_an_error(self):
        with pytest.raises(ValueError):
            parse_bracket("probably higher")


class TestExpectation:
    def test_renormalises_a_book_that_does_not_sum_to_one(self):
        # Quotes summing to 0.5 must not halve the expectation.
        half = ladder([("$2.00-$2.50", 0.25), ("$2.50-$3.00", 0.25)])
        assert half.expectation() == pytest.approx(2.50)

    def test_a_point_mass_is_its_own_midpoint(self):
        one = ladder([("$2.50-$3.00", 1.0)])
        assert one.expectation() == pytest.approx(2.75)
        assert one.dispersion() == pytest.approx(0.0)

    def test_dispersion_grows_as_the_book_spreads_out(self):
        tight = ladder([("$2.50-$2.75", 0.9), ("$2.75-$3.00", 0.1)])
        wide = ladder([("$1.50-$2.00", 0.5), ("$3.50-$4.00", 0.5)])
        assert wide.dispersion() > tight.dispersion()

    def test_the_real_october_book(self):
        dist = ladder(OCTOBER, volume=17_159.0)
        assert dist.book_sum == pytest.approx(0.9535, abs=1e-3)
        # Around the level the live board showed on the day.
        assert 2.4 < dist.expectation() < 2.8


class TestTailAssumption:
    def test_open_tails_move_the_answer_and_sensitivity_shows_it(self):
        dist = ladder(OCTOBER, volume=17_159.0)
        rows = expectation_sensitivity(dist, spans=(0.25, 1.00))
        # A wider assumed tail pushes both tails outward; they do not cancel,
        # because the upper tail carries more mass than the lower one here.
        assert rows[0]["expected"] != rows[1]["expected"]
        assert rows[1]["dispersion"] > rows[0]["dispersion"]

    def test_a_ladder_with_no_tails_is_unaffected_by_the_assumption(self):
        closed = ladder([("$2.00-$2.50", 0.5), ("$2.50-$3.00", 0.5)])
        rows = expectation_sensitivity(closed, spans=(0.25, 2.00))
        assert rows[0]["expected"] == pytest.approx(rows[1]["expected"])


class TestRefusals:
    def test_a_clean_ladder_is_usable(self):
        good = ladder(
            [("$2.00-$2.25", 0.2), ("$2.25-$2.50", 0.5), ("$2.50-$2.75", 0.3)],
            volume=50_000.0,
        )
        assert good.refusals() == []
        assert good.assess()["withheld"] is False

    def test_a_book_far_from_one_is_refused(self):
        loose = ladder([("$2.00-$2.25", 0.4), ("$2.25-$2.50", 0.4)], volume=50_000.0)
        assert any("sums to" in r for r in loose.refusals())

    def test_mass_in_the_tails_is_refused(self):
        tails = ladder(
            [("<$2.00", 0.45), ("$2.00-$2.25", 0.10), ("$2.25+", 0.45)], volume=50_000.0
        )
        assert any("tails" in r for r in tails.refusals())

    def test_thin_volume_is_refused(self):
        thin = ladder([("$2.00-$2.25", 0.5), ("$2.25-$2.50", 0.5)], volume=100.0)
        assert any("volume" in r for r in thin.refusals())

    def test_a_refused_ladder_withholds_but_still_reports_why(self):
        thin = ladder([("$2.00-$2.25", 0.5), ("$2.25-$2.50", 0.5)], volume=100.0)
        out = thin.assess()
        assert out["withheld"] is True
        assert out["refusals"]
        # The level is still computed; the caller is told not to quote it.
        assert out["expected"] is not None

    def test_an_empty_ladder_is_refused_without_dividing_by_zero(self):
        empty = ImpliedDistribution(tenor="t", settles=None, source_index="x")
        assert empty.refusals() == ["no open brackets"]
        assert empty.assess()["expected"] is None


class TestFromEvent:
    def test_reads_quotes_source_and_date_and_skips_closed_markets(self):
        event = {
            "endDate": "2026-10-31T12:00:00Z",
            "markets": [
                {
                    "groupItemTitle": "$2.50-$2.75",
                    "question": "Will the Ornn H100 Index be between $2.50 and $2.75 on October 31, 2026?",
                    "outcomePrices": '["0.41", "0.59"]',
                    "volumeNum": 900.0,
                },
                {
                    "groupItemTitle": "<$2.50",
                    "question": "Will the Ornn H100 Index be less than $2.50 on October 31, 2026?",
                    "outcomePrices": '["0.59", "0.41"]',
                    "volumeNum": 800.0,
                },
                {
                    "groupItemTitle": "$9.00+",
                    "question": "settled",
                    "outcomePrices": '["0.0", "1.0"]',
                    "closed": True,
                },
            ],
        }
        dist = distribution_from_event(event, tenor="end Oct 2026")
        assert len(dist.brackets) == 2, "closed markets must not enter the ladder"
        assert dist.source_index == "Ornn H100 Index"
        assert dist.settles is not None and dist.settles.isoformat() == "2026-10-31"
        assert dist.book_sum == pytest.approx(1.00)
        # Sorted with the open-below bracket first.
        assert dist.brackets[0].label == "<$2.50"

    def test_an_event_with_nothing_open_yields_an_empty_ladder(self):
        dist = distribution_from_event({"markets": [{"groupItemTitle": "<$2", "closed": True}]}, "t")
        assert dist.brackets == []
        assert dist.refusals() == ["no open brackets"]
