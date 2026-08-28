# GPU Rental Price Index — Methodology

**Version 1.0.0**
**Administrator:** reference implementation, not a live benchmark
**Status:** demonstration. Do not settle anything against these values.

---

## 1. What the index is for

The index answers one question, once per business day, for each covered GPU:

> What did it cost to rent one of these, on demand, in the United States,
> today?

It is designed as if it were going to be referenced by a cash-settled
derivative. That constraint drives every decision below. A market-intelligence
number can be approximately right and quietly revised. A settlement number has
to be reproducible years later, defensible against a counterparty who lost
money on it, and resistant to a participant who wants it somewhere else.

## 2. Coverage

| Index | Benchmark good | Status |
|---|---|---|
| `GIX-H100` | NVIDIA H100 SXM 80GB | published |
| `GIX-H200` | NVIDIA H200 SXM 141GB | published |
| `GIX-A100` | NVIDIA A100 SXM 80GB | unstable — flickers across the dispersion gate |
| `GIX-B200` | NVIDIA B200 SXM 180GB | published |
| `GIX-MI300X` | AMD MI300X 192GB | withheld — insufficient providers |

`GIX-MI300X` is included deliberately. Only one venue in the sample publishes
MI300X pricing, so it fails the provider-count gate every day and prints
nothing. An index that reported a number here would be reporting one vendor's
rate card dressed up as a market.

## 3. The benchmark-equivalent contract

An index over a heterogeneous good is meaningless without a standard unit.
"An H100-hour" is not one good: a PCIe card on Ethernet in a spare-capacity
marketplace and an SXM card on an NVLink fabric under a datacentre SLA differ
in price by a factor of three, and they are not substitutes for the buyer.

Every index therefore defines exactly one good, and every input is restated as
that good or discarded. For `GIX-H100`:

> 1× NVIDIA H100 SXM 80GB, on-demand (no commitment), deployed in an 8-GPU
> node with NVLink interconnect, US region, dedicated and non-preemptible,
> priced in USD per GPU-hour, excluding persistent storage, egress, support
> tiers, and any committed-use or credit discount.

This mirrors how physical commodity benchmarks work: Platts assesses a
standard cargo, the Baltic indices assess a standard route. The standard good
is the benchmark; everything else is a spread to it.

## 4. Restating inputs — and why this is the weak point

Non-conforming attributes are restated with multiplicative factors. A factor
above 1.00 means the observed good is cheaper than the benchmark good and is
marked up to be comparable.

| Attribute | Observed | Factor | Reasoning |
|---|---|---|---|
| Form factor | PCIe → SXM | 1.18 | Lower memory bandwidth, no NVLink fabric |
| Form factor | unknown → SXM | 1.09 | Midpoint; also raises an uncertainty flag |
| Interconnect | Ethernet → NVLink | 1.12 | Cannot serve multi-node training |
| Interconnect | none → NVLink | 1.15 | Single-node only |
| Commitment | spot → on-demand | 1.45 | Preemption risk the benchmark good lacks |
| Commitment | community → on-demand | 1.30 | No uptime guarantee, unvetted hosts |
| Commitment | reserved → on-demand | 1.25 | Removes the duration discount |
| Node size | 1–2 GPU → 8 GPU | 0.92–0.95 | Removes the fractional-rental premium |

**These factors are the most criticisable thing in this document, and that is
deliberate.** They are calibrated judgement, not estimated spreads. Estimating
them properly needs paired observations of the same hardware sold both ways at
the same moment, and almost nobody publishes that. Three things keep the
judgement honest rather than hidden:

1. Every factor is stated numerically in one file (`src/gpuidx/spec.py`), so a
   disputing counterparty argues with a number rather than with a black box.
2. Factors compose multiplicatively and independently. Interaction effects
   certainly exist, but a wrong interaction term is worse than an absent one.
3. Any input needing more than **1.75×** cumulative adjustment is discarded.
   Past that point the number describes the adjustment schedule rather than
   the market.

**One factor has since been validated, and one has been shown to be
uncalibratable.** Where a venue sells identical hardware under two commitment
types, the ratio is a direct observation of what it charges for the difference
— but only when that ratio is a price rather than a policy. `gpuidx calibrate`
measures it:

| Venue | Tier | Pairs | Observed | Asserted | Error | CV | Verdict |
|---|---|---|---|---|---|---|---|
| RunPod | community | 41 | 1.316 | 1.30 | +1.2% | 0.93 | market-determined, supports the factor |
| DataCrunch | spot | 54 | 2.000 | 1.45 | +37.9% | 0.0001 | administered policy, excluded |

RunPod's community-to-secure ratio varies from 0.31 to 7.58 across models. That
dispersion is what a real spread looks like, and its median lands within 1.2%
of the asserted 1.30 — the factor survives contact with evidence.

DataCrunch prices spot at *exactly* half of on-demand on all 54 of its
instance types, a coefficient of variation of 0.0001. That is a discount
schedule, not a market-clearing price, and it says nothing about the value of
preemption risk. An earlier version of this document claimed it as the one
place the spot factor could be checked; that claim was wrong, and the check it
promised is not available anywhere in public data.

Administered quotes are also excluded from the index outright. A price that is
a fixed function of another price already in the sample is a duplicate: it
double-counts one venue's view while adding noise through an adjustment factor
it cannot itself calibrate. Detection is automatic — a near-zero coefficient of
variation across a venue's SKUs — and every exclusion is flagged on the run.

**The spot factor of 1.45 therefore remains asserted and unvalidated**, and
should be read as the weakest number in this document.

**How much the schedule actually moves the fixing** is measured by
`gpuidx sensitivity`, which recomputes each index from inputs that conformed
to the benchmark contract as observed and discards every adjusted one:

| Index | Published | Conforming only | Shift | Conforming inputs | Adjusted weight |
|---|---|---|---|---|---|
| GIX-H100 | $3.115 | $3.029 | **+2.8%** | 10 of 52 | 82% |
| GIX-H200 | $3.400 | — | — | 6 of 14 | 50% |
| GIX-A100 | $2.179 | — | — | 7 of 33 | 86% |
| GIX-B200 | $5.676 | — | — | 4 of 13 | 60% |
| GIX-MI300X | — | — | — | 0 of 2 | 100% |

Two readings, and the second is uncomfortable.

For `GIX-H100` the schedule is influential but not decisive: 82% of
contributing weight rests on adjusted inputs, yet removing them entirely moves
the fixing by 2.8%. The adjustments are doing what they are supposed to do —
making heterogeneous supply comparable — rather than manufacturing the answer.

For every other index there is **no counterfactual at all**. Too few venues
sell the benchmark configuration natively to clear the publication gates, so
those indices do not merely lean on the adjustment schedule; they exist
because of it. That is a materially different claim from the one `GIX-H100`
supports, and it is disclosed rather than averaged away.

**Region is screened, not adjusted.** Cross-border price differences reflect
power costs, tax regimes, and latency to demand — a single scalar cannot
honestly collapse that. Disclosed non-US capacity is discarded. Venues that
publish one global rate card without regional attribution are admitted, since
excluding them would remove most of the rate-card tier; this is a known
compromise and is recorded as such.

## 5. The waterfall

Inputs are ranked by how much they prove, following the standard benchmark
pattern of preferring transactions to quotes and quotes to judgement.

| Tier | Definition | Weight | Sources here |
|---|---|---|---|
| 1 — executable | Offer a buyer can transact against right now, with observable capacity | 1.00 | Vast.ai offers above a reliability floor; Shadeform SKUs with confirmed live regions |
| 2 — rate card | Published on-demand price, capacity not confirmed at capture | 0.60 | RunPod, DataCrunch, Shadeform SKUs without live regions |
| 3 — judgement | Curated or carried-forward price standing in for an unobservable market | 0.25 | Hyperscaler list prices |

A value resting entirely on tier 2 and 3 is published but flagged
`no_executable_input`. The honest reading of such a print is "this is what
vendors say they would charge", which is not the same claim as "this is what
capacity traded at".

## 6. Estimation

Per index, per day:

1. **Collapse to provider medians.** Each provider contributes one
   observation regardless of how many SKUs it lists. A venue publishing forty
   variants of one box gets one vote, not forty.
2. **Screen on median absolute deviation.** Providers more than 3.0 robust
   sigma from the cross-provider median are excluded. MAD is used rather than
   standard deviation because it has a 50% breakdown point — the screen cannot
   be defeated by the outlier it exists to catch. Screening is suppressed
   below four providers, where it would remove signal rather than noise.

   When half or more of the providers quote *exactly* the same price, MAD
   collapses to zero and the sigma test is undefined. That is not a rare
   corner; it is the most dangerous configuration the screen faces, because a
   tight consensus is precisely when one extreme quote drags the mean
   furthest. In that case the screen falls back to a symmetric ratio band and
   excludes anything more than 3x above or below the consensus. The band is a
   ratio rather than a percentage because a percentage test is capped at 100%
   on the downside and could never catch one cent against a three dollar
   consensus. It is deliberately loose: genuinely cheap capacity exists, and
   this is a backstop against the absurd rather than a second opinion on where
   the market is.
3. **Weight by tier, then cap.** No single provider may carry more than **35%**
   of total weight. The cap is applied iteratively, since capping one provider
   raises everyone else's share. Below 1/0.35 ≈ 3 providers the cap is
   unsatisfiable and is skipped; such an index fails the provider gate anyway.
4. **Take the weighted mean** of surviving provider medians.

Aggregator attribution matters here. Shadeform resells roughly twenty
independent clouds through one feed. Counting it as a single provider would
understate market breadth; treating its rows as twenty independent providers
would overstate independence, since one feed outage removes them all at once.
Inputs are attributed to the underlying cloud the buyer actually transacts
with, and the weight cap bounds any single one. **The correlated-failure risk
of that single feed is a real residual exposure and is not fully mitigated.**

## 7. Publication gates

A value is published only if **all** of these hold. Otherwise the index prints
`withheld` with the failing gate recorded.

| Gate | Threshold |
|---|---|
| Contributing providers | ≥ 4 |
| Contributing observations | ≥ 8 |
| Robust dispersion (MAD/median) | ≤ 0.45 |
| Single-provider weight share | ≤ 35% |

**Withholding is a first-class outcome.** A gap in the series is a fact about
the market; an interpolated value is a fiction about it. Carrying yesterday's
number forward on a thin day is exactly the behaviour that makes a benchmark
manipulable — it converts "nobody traded" into "the price was unchanged",
and it rewards whoever stayed quiet.

## 8. Data quality

Checks that catch failures which *look like valid data*, since a feed
returning HTTP 500 is caught by the collector and a feed returning yesterday's
prices forever is not:

- **Stalled feed** — identical quote fingerprints across 3+ consecutive runs.
  Fingerprints deliberately exclude capture time so an unchanged rate card
  hashes identically day over day.
- **Provider dropout** — a source returning under 50% of its recent average
  row count. Partial truncation is more dangerous than total failure, because
  total failure is obvious.
- **Stale capture** — event time more than 6 hours behind the run, which
  catches a live feed serving a cached response.
- **Level shift** — day-over-day move beyond 15%. This *flags* but does not
  block: real markets gap, and a benchmark that suppresses genuine moves is
  worse than one that surfaces them. It requires sign-off, not suppression.
- **Adjustment dominance** — over half of inputs needing 25%+ cumulative
  adjustment, meaning the value reflects section 4 as much as observed prices.

## 9. Revisions

The store is append-only and bitemporal. Two clocks are tracked separately
throughout:

- **event time** — when the fact was true in the market (`index_date`)
- **knowledge time** — when this system first knew it (`published_at`)

A correction inserts a new revision and stamps its predecessor with
`superseded_at`. Nothing is ever overwritten. This makes the query a
settlement dispute actually needs answerable months later:

```
gpuidx as-of GIX-H100 2026-08-25 2026-08-26T09:00:00Z
```

— *what did the tape say for the 25th, as known on the morning of the 26th?*
A store that overwrites in place cannot answer that, which means it cannot
support a contract that settled against the original print.

Corrections carry a mandatory reason, recorded on the revision. A production
administrator would additionally need a published revision policy stating the
window inside which corrections are made at all, since a settled contract
cannot be un-settled — beyond that window the erroneous print stands and is
annotated rather than replaced.

## 10. Known limitations

Stated plainly, because a methodology document that only lists strengths is a
marketing document.

1. **The index measures the neocloud spot tail, not the GPU market.** The
   economically dominant volume is private multi-year bilateral capacity deals
   that never appear on any rate card. Those contracts price differently and
   are invisible here. Anything settling against this index carries that basis
   risk.

2. **The hyperscaler tier is not one tier, and the treatment of it is a
   judgement call.** AWS `p5.48xlarge` prices at $6.88/GPU-hour and survives
   the outlier screen as a contributing input. GCP ($10.98) and Azure ($12.29)
   are screened at 5.8 and 6.8 robust sigma against a neocloud median of $3.25.
   Screening two of the three largest compute vendors out of a compute price
   benchmark is defensible — their list rates arguably describe their own
   pricing power rather than the market — but it is not obviously correct, and
   a different administrator could justify a separate hyperscaler benchmark
   instead. See [docs/FINDINGS.md](docs/FINDINGS.md) section 1.

3. **Most adjustment factors remain unvalidated.** Section 4 tests the two
   that can be tested: the community factor survives at +1.2% against 41
   observed pairs, and the spot factor turns out to be uncalibratable from
   public data because the only venue quoting both tiers administers the
   spread rather than pricing it. The form-factor, interconnect, and node-size
   factors have no observable check at all and remain pure judgement.

4. **Sample instability.** Two collections three minutes apart produced an
   identical `GIX-H100` value but moved `GIX-A100` by 8% (from $2.014 to
   $2.175), because the contributing provider count changed from 9 to 8.
   Nothing about the market changed in those three minutes. A production
   version needs a defined capture window at a fixed time rather than
   "whenever the job ran", and must quantify intraday sampling variance before
   any daily delta is read as signal.

5. **No transaction data at all.** Every input is an offer or a rate card.
   Nobody here observes a completed rental at a price. That is the single
   largest gap between this and a benchmark that could responsibly settle a
   contract, and no amount of estimator sophistication closes it — it requires
   commercial agreements with venues to report executed volume.

6. **Correlated source failure.** One aggregator supplies most of the provider
   breadth. See section 6.

7. **The dispersion gate is not stable at current provider counts.** Two runs
   an hour apart on an unmoved market produced dispersion of 0.560 (10
   providers, withheld) and 0.365 (9 providers, published). MAD over roughly
   ten points is a coarse order statistic, and adding one mid-range provider
   shifts both the median and the MAD enough to flip the decision. The gate is
   correct in intent and too twitchy in practice. Remedies — hysteresis, an
   N-day persistence rule, or a dispersion estimate pooled over a trailing
   window — all require a real time series to calibrate, so this is recorded
   as an open defect rather than patched with a guessed constant. See
   [docs/FINDINGS.md](docs/FINDINGS.md) section 2.

8. **The outlier screen can conceal a bad input.** During development an
   incorrect AWS figure was extreme enough to be screened as an outlier, which
   tightened the surviving distribution and let `GIX-A100` publish at
   dispersion 0.365. Correcting the input to its true value put it inside the
   screen, raised dispersion to 0.560, and the index correctly stopped
   publishing. A wrong number that gets screened is more dangerous than one
   that does not, because the screen hides it. Screening is not a substitute
   for verifying inputs at the source, and the screen log must be reviewed
   rather than treated as self-healing.

9. **Screening and adjusting are applied inconsistently, and the consistent
   alternative would remove most of the coverage.** Region is screened because
   a scalar cannot honestly collapse power and tax regimes, and that reasoning
   applies just as well to form factor: a PCIe card on Ethernet is a different
   good from an SXM card on an NVLink fabric, not a discounted version of one.
   The methodology nonetheless adjusts form factor and interconnect while
   screening region.

   The obvious remedy is to screen instead of adjust — restrict inputs to the
   benchmark configuration and discard the rest. The sensitivity measurement
   in section 4 shows what that costs: only `GIX-H100` has enough natively
   conforming supply to clear its gates, and the other four indices would stop
   publishing entirely. Consistency is available at the price of 80% of the
   product.

   That is a genuine trade rather than an oversight, and the position taken
   here is to adjust, disclose the exposure per index, and publish the
   counterfactual where one exists. A settlement-grade version might well
   choose differently for `GIX-H100`, where the whole schedule is worth 2.8%,
   and decline to publish the rest at all.

## 11. Changing this document

The methodology version is stamped onto every published value. A change to any
factor, gate, or contract definition requires a version bump, so a series can
be split at a methodology change rather than silently spliced across one.
Consumers can then tell whether a move was the market or the method.
