# Findings

Observations from running the benchmark against live public pricing. Each one
is reproducible with `uv run gpuidx publish` followed by `gpuidx audit`.

All figures below are from the run of **2026-08-27**, which collected 577 raw
observations across ~20 clouds and normalised 163 of them.

---

## 1. AWS has repriced into the neocloud range. GCP and Azure have not.

This is the finding I did not expect, and it only appeared after I went back
and verified the hyperscaler rate card against source rather than trusting a
remembered figure.

| Venue | H100 on-demand, per GPU-hour | Screened? |
|---|---|---|
| Neocloud median | $3.25 | — |
| **AWS** `p5.48xlarge` | **$6.88** | no — contributes |
| GCP `a3-highgpu-8g` | $10.98 | yes, 5.8σ |
| Azure `ND96isr H100 v5` | $12.29 | yes, 6.8σ |

At $6.88 AWS is expensive relative to the neocloud tail but not categorically
outside it, and it survives the outlier screen to become a contributing input.
GCP and Azure, at 1.6× and 1.8× AWS respectively, are screened out every run.

The naive version of this finding — the one I had written before checking —
was "hyperscalers are a separate market", based on an AWS figure of
$12.29/GPU-hr. That number was launch-era pricing. AWS has cut the p5 rate
roughly 44% since, and the correct number tells a different and more specific
story: **the hyperscaler tier is not one tier.** One of the three has moved to
compete with the neoclouds on list price; the other two have not.

Anyone building a benchmark here has to decide whether GCP and Azure list
prices are evidence about the market or evidence about GCP and Azure. This
implementation screens them, which is a defensible choice and not an obviously
correct one.

## 2. The A100 market has fragmented — and the gate that detects it flickers

`GIX-A100` draws from ten providers spanning a 3.4x range:

```
vastai                  $0.81 - $1.03   <- marketplace, aged hardware
datacrunch              $1.28
shadeform:denvr         $1.41
shadeform:massedcompute $1.53
runpod                  $1.70
shadeform:crusoe        $2.06
shadeform:lambdalabs    $2.70
shadeform:excesssupply  $3.00
curated:aws             $3.43
shadeform:paperspace    $3.50            <- managed, SLA-backed
```

This looks like an aging-hardware effect. As a generation is superseded,
surplus capacity gets dumped onto marketplaces while managed providers keep
charging for the service wrapper rather than the silicon. The good stops being
fungible, and a single-price index stops being meaningful. H100 shows the same
pattern at a milder 0.35-0.38 dispersion; watching that number rise would be an
early warning that the H100 index is heading the same way.

**But the gate meant to detect this is not stable enough to act on.** Two runs
an hour apart, on a market that did not move:

| Run | Providers | Median | Dispersion | Decision |
|---|---|---|---|---|
| A | 10 | $1.880 | **0.560** | withheld |
| B | 9 (Lambda absent) | $1.699 | **0.365** | published |

One provider entering or leaving flips the publication decision, and not
because it was an outlier — Lambda at $2.70 sits mid-range. The cause is that
median absolute deviation over ~10 points is a coarse order statistic. Going
from 9 to 10 points moves both the median and the MAD from a single order
statistic to an average of two straddling a gap in the distribution, and the
ratio jumps 54%.

This is a design defect, not a data problem, and it is the kind that only
appears once you run the thing repeatedly rather than once. A production
version needs one of:

- **hysteresis** — a higher threshold to enter the withheld state than to
  leave it, so the index does not oscillate;
- **a persistence rule** — withhold only after N consecutive breaches;
- **a smoother dispersion estimate** — a trimmed interquartile ratio, or MAD
  pooled over a trailing window rather than computed on a single day's cross
  section.

All three need a real time series to calibrate against, which is the immediate
reason the pipeline now runs daily and archives every input. Fixing this is the
next piece of work, and it is deliberately not fixed by guessing a constant.

There is also a mechanical trap worth naming. With an *incorrect* AWS figure
present, AWS was screened as an outlier, the surviving distribution looked
tighter, and the index published at 0.365. Correcting the input to its true
value put it inside the screen and pushed dispersion to 0.560. **A wrong number
that gets screened is more dangerous than a wrong number that does not**,
because the screen hides it. Screening is not a substitute for verifying
inputs at source.

## 3. Half the "market data" here is a pricing policy

Two venues sell identical hardware under two commitment types, which should
let the commitment adjustment factors be estimated rather than asserted. Only
one of them actually can.

| Venue | Tier | Pairs | Observed ratio | Asserted | CV |
|---|---|---|---|---|---|
| RunPod | community vs secure | 41 | 1.316 | 1.30 | **0.93** |
| DataCrunch | spot vs on-demand | 54 | 2.000 | 1.45 | **0.0001** |

RunPod's ratio ranges from 0.31 to 7.58 across models — genuine dispersion,
because peer-supplied capacity clears differently for a 4090 than for an H200.
Its median lands within 1.2% of the factor the methodology asserts, which is
about as good as a judgement call gets.

DataCrunch prices spot at exactly 50.00% of on-demand on every single SKU. That
is not a market observation; it is a rule in a config file somewhere. Treating
it as evidence about the price of preemption risk would be circular, and using
it to calibrate the spot factor would be calibrating against an assumption.

Two consequences:

1. **The spot factor stays unvalidated.** No venue in public data prices
   preemptible capacity in a way that reveals what preemption is worth.
2. **Administered quotes are excluded from the index.** A price that is a fixed
   multiple of another price in the same sample is a duplicate observation
   dressed as an independent one. The pipeline now detects them by coefficient
   of variation and drops them, flagging each exclusion.

The general lesson is worth more than the specific finding: a feed can be
live, well-formed, high-volume, and still contain no information. Volume of
data is not evidence, and nothing about the shape of the payload tells you
which kind you have.

## 4. Sampling variance is the unglamorous risk

Two runs three minutes apart produced an identical `GIX-H100` fixing but moved
`GIX-A100` by 8% ($2.014 → $2.175), purely because the contributing provider
count changed from 9 to 8 between collections.

Nothing about the market changed in those three minutes. The index moved
because the sample did. Before any daily delta can be read as signal, the
capture has to happen in a fixed window at a fixed time, and the intraday
sampling variance has to be measured and published alongside the level.

## 5. Only one venue quotes MI300X

`GIX-MI300X` withholds every single run: one provider, two observations,
against gates of four and eight. There is no honest number to print. An index
that reported one here would be republishing RunPod's rate card and calling it
a market.

Worth stating because a commercial benchmark under pressure to show broad
coverage faces exactly this temptation, and the failure is invisible to
consumers unless the methodology discloses provider counts per index. This one
publishes them on every value.

## 6. Nothing here is a transaction

Every input is an offer or a rate card. Vast.ai offers are executable — a buyer
could transact against them right now — but nobody in this data set observes a
completed rental at a price.

That gap does not close with better statistics. It closes with commercial
agreements under which venues report executed volume, which is precisely the
arrangement that separates a market-data product from a benchmark that can
responsibly settle a contract.

---

## Reproducing

```bash
uv run gpuidx publish
uv run gpuidx audit GIX-A100 $(date -u +%F)     # the dispersion story
uv run gpuidx audit GIX-H100 $(date -u +%F)     # the hyperscaler story
```

Values will differ from those above — this reads a live market.
