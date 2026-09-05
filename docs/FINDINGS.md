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

**Update, 2026-09-05: AWS has since crossed the screen, and it sits exactly on
the threshold.** The table above is correct for 2026-08-27 and stayed correct
through 2026-09-02. On 2026-09-03 AWS was screened at **3.0 robust sigma** —
the threshold is 3.0 — and it has been screened since. The rate card did not
move; it is $6.88 on every one of those runs. What moved was the neocloud
median around it.

That is worse than it first looks. The index does not merely include or exclude
a hyperscaler on the merits: it flips between being a neocloud-only benchmark
and a neocloud-plus-AWS benchmark, day to day, on the third significant figure
of a robust sigma. Two consumers comparing the series across that boundary are
comparing two different goods, and nothing in the published value says so.

There are three honest responses and this implementation has taken none of them
yet. Declare the index neocloud-only and screen the hyperscaler tier by rule
rather than by outlier distance, which is what the tier arguably deserves — a
hyperscaler list price is a different kind of evidence, not a distant sample of
the same kind. Or publish a separate hyperscaler benchmark, which is the option
section 10.2 of the methodology already concedes a different administrator
could justify. Or keep the current behaviour and flag the runs where the
composition changes, so at least the flip is visible.

The general lesson is the one the estimator keeps teaching: a threshold test
applied to a quantity that drifts will eventually sit on its own boundary, and
when it does, the decision it makes is noise wearing the costume of a rule.

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

## 6. There is no GPU forward curve, and the usual proxy does not work

A GPU-hour cannot be stored. You cannot buy one today, hold it, and deliver it
in six months; an unused hour is gone. That removes the machinery most
commodity forward pricing rests on. For a storable good the forward is pinned
by arbitrage — buy spot, finance, store, deliver. Here there is no such trade,
so no-arbitrage says nothing and

    F(T) = E[S(T)] + risk premium

with nothing to pin either term. Compute belongs with electricity, weather, and
freight rather than with gold and crude. Term structure is driven by supply-side
physics — fab and HBM capacity, datacentre power, and above all the
next-generation release cadence — not by carry. A B200 ramp mechanically
depresses the H100 forward curve in a way no storage-cost model would predict.

The obvious proxy is the committed-use discount: vendors sell one- and
three-year commitments below on-demand, and that discount is a real price.
Inverting it for an implied constant decline rate gives:

| Vendor | Tenor | Price vs on-demand | π=0% | π=10% | π=20% |
|---|---|---|---|---|---|
| AWS | 1y | 60% | 67.6% | 58.3% | 45.4% |
| AWS | 3y | 40% | 52.5% | 47.3% | 41.2% |
| GCP | 1y | 63% | 63.5% | 53.3% | 39.3% |
| GCP | 3y | 45% | 46.6% | 41.2% | 34.9% |

π is the share of the discount attributed to lock-in and price certainty
rather than to expectations. **Every cell is implausible.** Taken literally,
the mildest reading has GPU spot falling 35% a year and the most aggressive
has it falling 68%; projecting $3.05/GPU-hr forward at the naive rate gives
$0.10 in three years.

That is not a forecast. It is a proof that the proxy is wrong, and it fails in
two identifiable ways:

1. **The discount is not mostly expectation.** It bundles lock-in, the vendor's
   value from guaranteed utilisation, and the buyer's price of certainty. One
   observed discount is one equation in four unknowns, and no public data
   separates them.
2. **The tenors disagree.** Under a constant decline rate, a vendor's one-year
   discount determines its three-year one. AWS's imply 58.3% and 47.3%; GCP's
   imply 53.3% and 41.2%. A spread of 0.23 in both cases says lock-in cost
   grows with term — which it obviously does — so a constant-rate model is the
   floor of a more honest term-dependent one.

There is a third problem the arithmetic cannot see: AWS Savings Plans and GCP
CUDs are *general compute* instruments, not GPU-specific. Using them as GPU
term structure assumes the commitment economics of a GPU node match those of a
web server, which is not obviously true and is probably false.

The honest conclusion is that a GPU forward curve cannot be derived from
public data at all. It has to be observed, which requires either a liquid
futures market or commercial access to bilateral term deals. `gpuidx forward`
exists to make the size of that gap explicit rather than to paper over it with
a fitted curve.

## 7. The manipulation screen had a hole in it, and consensus opened it

The estimator screens outliers on median absolute deviation specifically
because MAD has a 50% breakdown point — you cannot defeat it with the outlier
it exists to catch. Feeding it deliberately hostile inputs found a case where
it stopped working entirely.

```
four providers at $3.00, one at $1,000,000
median = $3.00
MAD    = median(0, 0, 0, 0, 999997) = 0
```

With MAD at zero the sigma test is undefined, and the implementation returned
without screening anything. The published value was **$200,002**.

The trap is that the failure requires *agreement*. As long as honest providers
differ even slightly, MAD is positive and the screen works, which is why the
original test — built on prices of 3.00, 3.10, 2.90, 3.05 — passed happily. The
screen only broke when the market agreed exactly, which is also the moment a
single extreme quote does the most damage. A tight consensus and a broken
outlier screen arrive together.

The fix falls back to a symmetric ratio band when MAD is zero: anything more
than 3x above or below the consensus is screened. The first attempt used a
percentage deviation and was wrong twice over. It screened a provider 50%
below consensus, which is ordinary market structure rather than manipulation —
spare-capacity marketplaces really do clear at half a managed cloud. And a
percentage test is capped at 100% on the downside, so it could never have
caught a one-cent quote against a three-dollar consensus at all. A ratio band
is symmetric in the way the problem is.

Two things worth taking from it. Robust statistics have degenerate cases, and
the degenerate case is not always the harmless one — here it coincided exactly
with maximum exposure. And a test suite built from realistic-looking data will
not find this; it took deliberately hostile inputs, which is a different
activity from testing that the happy path works.

**The systematic fix is `tests/test_properties.py`**, which generates markets
rather than choosing them and asserts ten invariants that must hold for any
market at all: the fixing never escapes the range of contributing prices, an
absurd quote in either direction is always screened, flooding a venue with
duplicate SKUs changes nothing, scaling every price scales the fixing exactly,
quote order is irrelevant, no provider exceeds the weight cap, and a failed
gate always blocks publication.

The generators round prices to a coarse grid deliberately. Sampling continuous
floats would make exact ties vanishingly unlikely, and the original defect is
reachable only through ties — which is the whole reason hand-written fixtures
missed it.

Two checks that the properties are worth having:

* Reverted against the original screen, `test_an_absurdly_high_quote_is_always
  _screened` and `test_an_absurdly_low_quote_is_always_screened` both fail, and
  Hypothesis minimises the first to four providers at $0.25 and one adversary
  at 100x. The properties catch the bug that hand-written tests missed.
* The properties also found a mistake in their own author. A first attempt
  asserted that a screened provider is always further from the median than any
  kept one, measured as absolute distance. Hypothesis produced
  `[0.25, 0.76, 0.76, 0.76, 1.28]`: MAD is zero, so the ratio branch runs, 0.25
  is screened at 3.04x while 1.28 is kept at 1.68x — even though 1.28 sits a
  hair further away in absolute terms. The screen was right and the stated
  invariant was wrong. Writing the property forced the invariant to be stated
  in the terms the code actually uses.

## 8. Four of the five indices exist only because of the adjustment schedule

The methodology concedes that its adjustment factors are judgement, bounds
them, and caps their cumulative effect. That is a defence of the containment,
not of the numbers, and it leaves the question a counterparty would actually
ask: *how far does the fixing move because of them?*

`gpuidx sensitivity` recomputes each index using only inputs that conformed to
the benchmark contract as observed, discarding every adjusted one:

| Index | Published | Conforming only | Shift | Conforming inputs | Adjusted weight |
|---|---|---|---|---|---|
| GIX-H100 | $3.115 | $3.029 | **+2.8%** | 10 of 52 | 82% |
| GIX-H200 | $3.400 | — | — | 6 of 14 | 50% |
| GIX-A100 | $2.179 | — | — | 7 of 33 | 86% |
| GIX-B200 | $5.676 | — | — | 4 of 13 | 60% |
| GIX-MI300X | — | — | — | 0 of 2 | 100% |

`GIX-H100` comes out well. Four fifths of its contributing weight rests on
adjusted inputs, and removing all of them moves the fixing 2.8%. The
adjustments are making heterogeneous supply comparable rather than
manufacturing an answer, which is what they are for.

The other four have **no counterfactual at all** — too few venues sell the
benchmark configuration natively to clear the publication gates. Those indices
do not lean on the adjustment schedule; they are produced by it. That is a
materially weaker claim than the one `GIX-H100` supports, and consumers of the
two should not treat them as the same kind of number.

This also settles an argument the methodology was having with itself. Region
is screened rather than adjusted, on the grounds that a scalar cannot honestly
collapse it, and the identical argument applies to form factor — which is
adjusted. The consistent position is to screen both and discard non-conforming
supply. The measurement prices that consistency: it would leave one index
standing and remove the other four. That is a real trade, not an oversight, and
naming its cost is more useful than resolving it by preference.

The counterfactual is not a better estimate, and the report says so. Dropping
adjusted inputs throws away most of the sample and biases toward whichever
venues happen to sell the benchmark configuration. It measures dependence on
judgement, and nothing else.

## 9. Nothing here is a transaction

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
