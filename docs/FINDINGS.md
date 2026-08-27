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

## 2. The A100 market has fragmented, and the dispersion gate catches it

`GIX-A100` **withholds**. Ten providers contribute, spanning:

```
vastai                  $1.032   <- marketplace, aged hardware
datacrunch              $1.281
shadeform:denvr         $1.406
shadeform:massedcompute $1.533
runpod                  $1.699
shadeform:crusoe        $2.060
shadeform:lambdalabs    $2.700
shadeform:excesssupply  $3.000
curated:aws             $3.431
shadeform:paperspace    $3.500   <- managed, SLA-backed
```

A 3.4× spread across ten venues, robust dispersion **0.560** against a 0.45
ceiling. The gate refuses to publish, and it is right to: there is no single
number that honestly represents both a $1.03 marketplace hour and a $3.50
managed hour.

This looks like an aging-hardware effect. As a generation is superseded,
surplus capacity gets dumped onto marketplaces while managed providers keep
charging for the service wrapper rather than the silicon. The good stops being
fungible, and a single-price index stops being meaningful. H100 shows the same
pattern at a milder 0.379 dispersion; watching that number rise over time
would be an early warning that the H100 index is heading the same way.

Note the mechanical subtlety: with the *incorrect* AWS figure, AWS was screened
as an outlier and the surviving distribution looked tighter (0.365, published).
Correcting the input made the index *stop* publishing. A wrong number that gets
screened is more dangerous than a wrong number that does not, because the
screen hides it.

## 3. Sampling variance is the unglamorous risk

Two runs three minutes apart produced an identical `GIX-H100` fixing but moved
`GIX-A100` by 8% ($2.014 → $2.175), purely because the contributing provider
count changed from 9 to 8 between collections.

Nothing about the market changed in those three minutes. The index moved
because the sample did. Before any daily delta can be read as signal, the
capture has to happen in a fixed window at a fixed time, and the intraday
sampling variance has to be measured and published alongside the level.

## 4. Only one venue quotes MI300X

`GIX-MI300X` withholds every single run: one provider, two observations,
against gates of four and eight. There is no honest number to print. An index
that reported one here would be republishing RunPod's rate card and calling it
a market.

Worth stating because a commercial benchmark under pressure to show broad
coverage faces exactly this temptation, and the failure is invisible to
consumers unless the methodology discloses provider counts per index. This one
publishes them on every value.

## 5. Nothing here is a transaction

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
