# gpuidx

A working daily GPU rental price benchmark, built from live public pricing
across ~20 clouds, with the governance machinery a settlement-grade index
needs: a waterfall, robust estimation, publication gates, and a bitemporal
audit trail.

Built as a study of what it takes to turn heterogeneous compute pricing into a
number something could settle against. **It is a demonstration. Do not settle
anything against these values.**

The interesting part is not the pipeline — it is [METHODOLOGY.md](METHODOLOGY.md),
particularly section 10.

## Quickstart

```bash
uv sync
uv run gpuidx publish
```

Live output from a real run:

```
+----------------- collection ------------------+
| run 1 for 2026-08-27                          |
| 562 raw observations -> 174 normalised quotes |
+-----------------------------------------------+
                              daily fixing
+---------------------------------------------------------------------+
| index      |  value | prov | obs |  disp | status                    |
|------------+--------+------+-----+-------+---------------------------|
| GIX-H100   | $2.954 |   11 |  50 | 0.375 | published                 |
| GIX-H200   | $3.540 |    7 |  23 | 0.398 | published                 |
| GIX-A100   | $2.014 |    9 |  58 | 0.365 | published                 |
| GIX-B200   | $5.787 |    6 |  36 | 0.276 | published                 |
| GIX-MI300X |     -- |    1 |   2 |    -- | withheld  min_providers   |
+---------------------------------------------------------------------+
```

## Commands

```bash
uv run gpuidx contracts                    # benchmark definitions and gates
uv run gpuidx publish                      # run one collection + fixing cycle
uv run gpuidx show GIX-H100                # the series
uv run gpuidx audit GIX-H100 2026-08-27    # every provider behind one value
uv run gpuidx revisions GIX-H100 2026-08-27
uv run gpuidx as-of GIX-H100 2026-08-27 2026-08-27T20:15:00Z
```

`audit` is the one worth looking at. It shows every contributing provider,
its weight and share, and every provider that was screened out and why.

## Sources

All unauthenticated public endpoints. No scraping, no credentials.

| Source | What it gives | Tier |
|---|---|---|
| [Shadeform](https://api.shadeform.ai/v1/instances/types) | ~19 clouds in one feed, with node size, interconnect, NVLink, per-region availability | 1 / 2 |
| [Vast.ai](https://console.vast.ai/api/v0/bundles/) | Marketplace offers with host reliability — the only executable prices here | 1 / 2 |
| [RunPod](https://api.runpod.io/graphql) | Secure and community rate cards on the same hardware | 2 |
| [DataCrunch](https://api.datacrunch.io/v1/instance-types) | On-demand and spot on identical hardware | 2 |
| `data/curated_rate_cards.json` | Hyperscaler list prices, hand-maintained | 3 |

Curated entries carry an `as_of` date and are dropped after 45 days, so a
forgotten catalogue degrades into missing data rather than a confidently wrong
number. The shipped values are indicative and need re-verification before they
mean anything.

## Layout

```
src/gpuidx/
  spec.py         benchmark contracts, adjustment factors, gates  <- the methodology
  normalize.py    restate a venue price as the benchmark good
  estimator.py    provider medians, MAD screen, weight caps, gates
  quality.py      stalled feeds, dropout, level shifts
  store.py        bitemporal append-only SQLite
  pipeline.py     the daily cycle, wired end to end
  providers/      one adapter per venue
```

`spec.py` holds every number a dispute would be argued over, in one file, on
purpose.

## What came out of building it

**The values land close to the commercial benchmarks.** Against Silicon Data's
published levels the same week: H100 $2.95 here vs $2.67 there, B200 $5.79 vs
$5.63, A100 $2.01 vs $1.61. Close enough to suggest the approach is sound;
different enough to show how much the answer depends on methodology choices
nobody can see from outside.

**Hyperscaler prices are not in the same market.** AWS, Azure, and GCP H100
list rates (~$11–12/GPU-hr) sit 5.8–6.8 robust sigma from the neocloud median
(~$3.25) and get screened out every run. The estimator is behaving correctly.
The open question is whether that means one market with a wide quality spread,
or two markets needing separate benchmarks — the data here points at two.

**One index withholds every day, and should.** Only one venue in the sample
publishes MI300X pricing. `GIX-MI300X` therefore prints nothing rather than
republishing a single vendor's rate card as a market price.

**Sampling variance is the unglamorous risk.** Two runs three minutes apart
gave an identical H100 fixing but moved A100 by 8%, purely because one
provider's rows changed between them (9 contributors, then 8). Before anyone reads a daily delta as
signal, the capture window has to be fixed and that variance quantified.

**No transaction data exists.** Every input is an offer or a rate card. That
gap is not closable with better statistics; it needs venues contractually
reporting executed volume.

## Tests

```bash
uv run pytest
```

28 tests. The ones that matter are in `test_estimator.py` — they encode the
adversarial cases: a provider flooding the feed with forty cheap SKUs, a
single absurd offer trying to drag the median, a venue trying to dominate a
thin index. And `test_store.py`, which asserts the property everything else
rests on: a corrected value never destroys what the tape said before the
correction.
