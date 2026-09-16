# Implied-curve readings

One dated file per day, written by `.github/workflows/implied.yml`.

**These are not this index.** Each reading is taken from prediction-market
bracket ladders that settle against the **Ornn H100 Index**, a competing
benchmark. They are recorded here because they are the only observable forward
expectation for compute that exists today, and because of what happens next.

**CME lists compute futures on 5 October 2026**, settling against **Silicon
Data's** index. From that date there will be two forward curves on the same
underlying, built by different constructions from different benchmarks. The
comparison is only possible if the "before" was written down while it was still
the present, which is the entire purpose of this directory.

Each file records, per tenor: the expected settlement level, the dispersion,
the book sum, the share of mass sitting in open-ended tails, the traded volume,
and whether the reading was **withheld** and why. A file in which every ladder
was withheld is still a fact about the market, so it is still written.

Read one with `gpuidx implied`; the module is `src/gpuidx/implied.py` and the
reasoning for the construction is in its docstring and in `forward.py`.
