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
| 577 raw observations -> 163 normalised quotes |
+-----------------------------------------------+
                              daily fixing
+---------------------------------------------------------------------+
| index      |  value | prov | obs |  disp | status                    |
|------------+--------+------+-----+-------+---------------------------|
| GIX-H100   | $3.082 |   12 |  51 | 0.379 | published                 |
| GIX-H200   | $3.566 |    7 |  24 | 0.398 | published                 |
| GIX-A100   |     -- |   10 |  45 | 0.560 | withheld  dispersion      |
| GIX-B200   | $5.823 |    6 |  39 | 0.294 | published                 |
| GIX-MI300X |     -- |    1 |   2 |    -- | withheld  min_providers   |
+---------------------------------------------------------------------+
```

Two of five indices decline to print. That is the system working.

## Commands

```bash
uv run gpuidx contracts                    # benchmark definitions and gates
uv run gpuidx publish                      # run one collection + fixing cycle
uv run gpuidx show GIX-H100                # the series
uv run gpuidx audit GIX-H100 2026-08-27    # every provider behind one value
uv run gpuidx revisions GIX-H100 2026-08-27
uv run gpuidx as-of GIX-H100 2026-08-27 2026-08-27T20:15:00Z
uv run gpuidx rebuild                      # restore the database from the archive
uv run gpuidx verify                       # recompute every value from its inputs
uv run gpuidx calibrate                    # test the adjustment factors against venue pricing
```

`audit` is the one worth looking at. It shows every contributing provider,
its weight and share, and every provider that was screened out and why.

## The durable record

The pipeline [runs daily](.github/workflows/daily.yml) and commits two things.
The SQLite database is not one of them — it is derived state, rebuilt from the
archive at the start of every run.

```
snapshots/2026-08-27T140500Z.jsonl.gz   every raw observation, exactly as the
                                        venue stated it, never modified
series/index_values.csv                 the publication record: one row per
                                        revision, append-only
```

The snapshots make a value reconstructible. The tape cannot be derived from
them, because it records *what was published and when*, including values later
superseded — re-deriving it from inputs would quietly erase the revision
history, which is the one thing a settlement dispute needs. Each tape row names
the snapshot it came from, so a day carrying two runs can still be checked.

`gpuidx verify` recomputes every published value from its own archived inputs
and fails if any no longer reproduces. It runs in CI on every fixing, which
means the series is not merely logged — it is auditable, and an altered
snapshot or an unversioned methodology change breaks the build.

## Sources

All unauthenticated public endpoints. No scraping, no credentials.

| Source | What it gives | Tier |
|---|---|---|
| [Shadeform](https://api.shadeform.ai/v1/instances/types) | ~19 clouds in one feed, with node size, interconnect, NVLink, per-region availability | 1 / 2 |
| [Vast.ai](https://console.vast.ai/api/v0/bundles/) | Marketplace offers with host reliability — the only executable prices here | 1 / 2 |
| [RunPod](https://api.runpod.io/graphql) | Secure and community rate cards on the same hardware | 2 |
| [DataCrunch](https://api.datacrunch.io/v1/instance-types) | On-demand and spot on identical hardware | 2 |
| `data/curated_rate_cards.json` | Hyperscaler list prices, hand-maintained | 3 |

Curated entries carry an `as_of` date, a `source_url`, and a `source_type`
recording how much the number is worth: the AWS rows are read from the feed
backing the EC2 pricing page, the GCP and Azure rows are cross-checked against
third-party aggregators and flagged as lower confidence. Everything here is
dropped after 45 days, so a forgotten catalogue degrades into missing data
rather than into a confidently wrong number.

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

Full write-up in [docs/FINDINGS.md](docs/FINDINGS.md). The short version:

**AWS has repriced into the neocloud range; GCP and Azure have not.** AWS
`p5.48xlarge` works out to $6.88/GPU-hour and survives the outlier screen as a
contributing input. GCP ($10.98) and Azure ($12.29) are screened at 5.8σ and
6.8σ against a neocloud median of $3.25. The hyperscaler tier is not one tier.

I only found this by re-verifying the rate card against source. My first pass
used a remembered AWS figure of $12.29 — launch-era pricing, since cut ~44% —
which produced the tidier but wrong conclusion that hyperscalers are simply a
separate market.

**The A100 market has fragmented, and the gate that detects it flickers.** Ten
providers span $0.81 to $3.50 per GPU-hour — aged hardware dumped on
marketplaces while managed providers keep charging for the service wrapper. But
two runs an hour apart, on a market that did not move, gave dispersion 0.560
(withheld, 10 providers) and 0.365 (published, 9 providers). One mid-range
provider entering flips the publication decision, because MAD over ~10 points
is too coarse an order statistic to gate on. That is a design defect, it only
shows up if you run the thing repeatedly, and fixing it properly needs a real
time series — which is why the pipeline now runs daily.

**A wrong number that gets screened is more dangerous than one that does not.**
With the incorrect AWS figure, AWS was extreme enough to be screened as an
outlier, which tightened the surviving distribution and let A100 publish.
Correcting the input put it inside the screen and stopped publication. The
screen hid the bad input rather than surfacing it. Screening is not a
substitute for verifying inputs at source.

**Half the "market data" here is a pricing policy.** Two venues sell identical
hardware under two commitment types, which should let the commitment
adjustment factors be estimated rather than asserted. RunPod's community-to-
secure ratio ranges 0.31–7.58 across models with a median of 1.316 — genuine
dispersion, and within 1.2% of the 1.30 the methodology asserts. DataCrunch
prices spot at exactly 50.00% of on-demand on all 54 of its SKUs, a coefficient
of variation of 0.0001. That is a rule in a config file, not a market
observation, and calibrating against it would be calibrating against an
assumption. The pipeline now detects administered pricing by dispersion and
excludes it, since a price that is a fixed multiple of another price in the
same sample is a duplicate observation dressed as an independent one.

A feed can be live, well-formed, high-volume, and contain no information.
Nothing about the shape of the payload tells you which kind you have.

**Sampling variance is the unglamorous risk.** Two runs three minutes apart gave
an identical H100 fixing but moved A100 8%, purely because contributing
providers went 9 to 8. Nothing about the market changed. A fixed capture window
is a prerequisite before any daily delta is read as signal.

**Nothing here is a transaction.** Every input is an offer or a rate card. That
gap does not close with better statistics — it closes with commercial
agreements under which venues report executed volume.

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
