"""Historical back-series reconstructed from archived public rate cards.

**Not the index.** Everything in this package produces a separate series --
``series/backfill_ratecards*.csv`` -- reconstructed from Internet Archive
captures of providers' public price pages. Those are list prices, not
transactions, read at whatever moments the archive happened to capture, from
whichever providers' pages the archive happened to keep. See docs/BACKFILL.md
for sources, coverage, restatement rules, known biases, and what the series
must not be used for.
"""
