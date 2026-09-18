# Related work

What the literature says about the problems this index runs into, and — where it
matters — which conclusions here were reached independently of it.

The distinction is deliberate. A citation is useful when it corroborates a
result or supplies a step this repository does not take. It is not useful as
borrowed authority, so nothing below is cited for a claim its authors do not
make.

---

## Bandi & Su (2026), *(Early) AI Compute Asset Pricing*

arXiv:[2607.12156](https://arxiv.org/abs/2607.12156) · Federico M. Bandi and
Yinan Su · August 2026

The first asset-pricing treatment of compute. Directly relevant to
[`src/gpuidx/forward.py`](../src/gpuidx/forward.py).

### Where it agrees with this repository

`forward.py` derives no forward curve from spot, on the grounds that a GPU-hour
cannot be stored, so the cash-and-carry relation

    F(T) = S · exp[(r + c − y)(T − t)]

has nothing to enforce it: there is no trade that buys spot, carries it, and
delivers later. **That argument was written here before this paper was read**,
and the paper establishes the same result formally, putting compute with
electricity rather than with oil:

> *"A GPU-hour that is not used today cannot be carried forward and delivered
> next month."*

The agreement is worth recording precisely because it is not borrowed.

### Where it goes further than this repository does

The paper supplies the constructive step that `forward.py` stops short of.
Reserved contracts already exist at many tenors, so a **synthetic** futures
price can be differentiated off the observed term rental curve:

    F_syn(T) = ∂/∂T [ (T − t) · Π(t→T) ]

where `Π(t→T)` is the fixed physical term rate over `[t, T]`. The distinction
that matters: **spot → forward is invalid; term structure → forward is valid.**

It then adds two adjustments this repository does not model:

- a **physical access wedge** `Δ(T) ≥ 0`, converging to zero at delivery, because
  a reserved contract guarantees *capacity* while a cash-settled future only pays
  money;
- a **risk premium** `λ(T)`, signed by assuming compute providers are the
  marginal hedgers, which pushes futures below expected spot.

**Why this repository still does not publish a curve.** The construction needs
transacted term rates across tenors. What is observable here is the published
committed-use discount (`data/committed_use.json`), which is a vendor headline
rate rather than a transacted one — each entry says so in its own note — and
which bundles expected decline, the buyer's premium for certainty, the vendor's
utilisation value and the cost of lock-in. One equation, several unknowns. So
the module inverts it for an implied decline *conditional on an assumed risk
premium* and publishes the sensitivity, because the width of that range is the
honest headline.

### Two caveats worth carrying

Their empirical premia — 7.5% (A100), 26.2% (H100), 11.2% (B200) annualised — are
measured against **synthetic** futures, not traded ones, which the authors state
plainly. And their primary data is the **Silicon Data** and **Ornn** indices, so
this is not a neutral reference for an independently constructed benchmark; it is
a useful one, and the provenance should be named rather than glossed.

---

## Xing (2026), *AI Token Futures Market: Commoditization of Compute and Derivatives Contract Design*

arXiv:[2603.21690](https://arxiv.org/abs/2603.21690) · Yicai Xing · March 2026

A design paper rather than an empirical one: it argues that inference tokens meet
commodity criteria and specifies a futures contract for them, including a
Standard Inference Token and settlement against a **Token Price Index**.

Relevant to this repository only by analogy — it draws the same parallel to
electricity and carbon markets that `forward.py` reaches for — but it is the
direct antecedent for the sibling benchmark; see
[`token-price-index`](https://github.com/henryzhangpku/token-price-index) and its
own related-work note.

**Read with its limits in view.** Single author, no empirical data, and the
headline claim that hedging cuts enterprise compute-cost volatility by 62–78%
comes from Monte Carlo over assumed mean-reverting jump-diffusion dynamics. It is
prior art on *contract design*, not evidence about prices.

---

## What is deliberately absent

No citation is offered for the estimator, the screens or the publication gates.
Those choices were made against this data and are defended by the measurements in
[`FINDINGS.md`](FINDINGS.md) and by `gpuidx robustness`, not by appeal to
anybody's paper.
