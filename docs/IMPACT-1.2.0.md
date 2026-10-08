# Methodology 1.2.0: what it would have done to the published history

Methodology 1.2.0 fixes two defects in 1.1.0 (METHODOLOGY section 12,
FINDINGS #13 and #14). It applies **forward**, from the first fixing a build
containing it publishes. No row already on the tape is restated or revised.
This page is the back-test that the change publishes instead: every live
fixing recomputed from the exact snapshot it names, under 1.2.0, and set
beside what was printed.

Every number here reproduces from the committed archive, with no network:

```bash
uv run gpuidx impact 1.2.0 --against 1.1.0 --days   # section A and C: the change itself
uv run gpuidx impact 1.2.0 --days                   # section B: against the tape as printed
```

"Differ" means the recomputed value moves by more than half a cent (the
tolerance `verify` uses), or publishes where the tape withheld or the
reverse, which is counted as a flip. The archive at the time of writing holds
33 live fixings per index: 21 published under 1.0.0 (27 August to
16 September 2026) and 12 under 1.1.0 (17 September, then 28 September to
8 October; there are no fixings between 18 and 27 September).

## Why the history is not restated

METHODOLOGY section 11 says a series is *split* at a methodology change,
never spliced across one, and that a value published under a version stays
reproducible under that version for good. Section 9's revision mechanism is
for correcting a value that was wrong *under the rules it was published
under*: a bad input, a broken feed. These values were not wrong under 1.1.0;
1.1.0 was wrong. Writing 1.2.0 revisions over them would put two methodologies
into one series with nothing on the face of the tape to show where the seam
is, which is exactly the splice section 11 forbids, and it would supersede
the values anyone reading the series has already used. So the 1.1.0 rows
stand, keep reproducing under 1.1.0 (`gpuidx verify` checks them every day),
and the size of the error is published here rather than quietly absorbed.

A reader who wants a consistent series across the split can take section A's
1.2.0 column for the 1.1.0 dates.

## A. Attributable to 1.2.0

1.2.0 against 1.1.0, both recomputed from the same snapshot, on the twelve
fixings published under 1.1.0. This isolates the two fixes from everything
else.

| Index | Fixings | Differ | Publish/withhold flips | Median move | Largest move |
|---|---|---|---|---|---|
| `GIX-H100` | 12 | 10 | 0 | 16.2% | -19.6% (2026-10-01) |
| `GIX-H200` | 12 | 6 | 0 | 3.1% | -10.9% (2026-10-08) |
| `GIX-A100` | 12 | 1 | 1 (withheld to $2.333, 2026-09-17) | -- | -- |
| `GIX-B200` | 12 | 0 | 0 | -- | -- |
| `GIX-MI300X` | 12 | 0 | 0 | -- | -- |

On the 21 fixings published under 1.0.0, 1.2.0 and 1.1.0 agree on every
index and every day. Before 1.1.0 the Vast.ai adapter recorded no machine or
host ids, so both versions hold Vast.ai out of every index as an unproven
book (FINDINGS #12), and both defects live entirely inside Vast.ai's vote.

## B. Against the tape as printed

All 33 fixings. For the 1.0.0 dates this mixes 1.2.0's two fixes with every
change 1.1.0 made (identity screen, currency and teaser screens, the book
floor, and Vast.ai held out for want of ids), so it says what a 1.2.0 series
would have looked like, not what 1.2.0 itself changed.

| Index | Fixings | Differ | Publish/withhold flips | Median move | Largest move |
|---|---|---|---|---|---|
| `GIX-H100` | 33 | 31 | 0 | 5.9% | -19.6% (2026-10-01) |
| `GIX-H200` | 33 | 24 | 0 | 9.2% | -12.2% (2026-09-09) |
| `GIX-A100` | 33 | 22 | 16 | 17.7% | +27.9% (2026-09-01) |
| `GIX-B200` | 33 | 16 | 0 | 8.1% | -13.6% (2026-09-15) |
| `GIX-MI300X` | 33 | 0 | 0 | -- | -- |

Every one of the 1.0.0-date differences is already there under 1.1.0 (section
A's last paragraph); `gpuidx impact 1.1.0 --days` lists them.

## C. Every 1.1.0 fixing 1.2.0 would change

The last two columns apply each fix alone on top of 1.1.0.

| Index | Date | Published (1.1.0) | 1.2.0 | Move | Region fix alone | Floor fix alone |
|---|---|---|---|---|---|---|
| `GIX-A100` | 2026-09-17 | withheld | $2.333 | flip | -- | flip |
| `GIX-H100` | 2026-09-17 | $3.045 | $3.191 | +4.8% | +29.0% | +4.8% |
| `GIX-H100` | 2026-09-28 | $3.188 | $3.342 | +4.8% | +2.1% | +4.8% |
| `GIX-H100` | 2026-09-29 | $3.142 | $3.291 | +4.7% | +1.1% | -- |
| `GIX-H100` | 2026-10-01 | $4.103 | $3.298 | -19.6% | -- | -19.6% |
| `GIX-H100` | 2026-10-02 | $3.943 | $3.314 | -15.9% | -- | -15.9% |
| `GIX-H100` | 2026-10-03 | $3.879 | $3.237 | -16.6% | -- | -16.6% |
| `GIX-H100` | 2026-10-04 | $3.969 | $3.315 | -16.5% | -- | -16.5% |
| `GIX-H100` | 2026-10-06 | $4.059 | $3.351 | -17.5% | -- | -17.5% |
| `GIX-H100` | 2026-10-07 | $4.081 | $3.312 | -18.9% | -- | -18.9% |
| `GIX-H100` | 2026-10-08 | $3.906 | $3.334 | -14.6% | -- | -14.6% |
| `GIX-H200` | 2026-09-28 | $3.563 | $3.503 | -1.7% | -- | -1.7% |
| `GIX-H200` | 2026-09-29 | $3.547 | $3.511 | -1.0% | -- | -1.0% |
| `GIX-H200` | 2026-10-03 | $3.705 | $3.534 | -4.6% | -- | -4.6% |
| `GIX-H200` | 2026-10-05 | $3.682 | $3.549 | -3.6% | -- | -3.6% |
| `GIX-H200` | 2026-10-06 | $3.650 | $3.557 | -2.5% | -- | -2.5% |
| `GIX-H200` | 2026-10-08 | $4.018 | $3.579 | -10.9% | -- | -10.9% |

## Reading it

**The early-October `GIX-H100` level goes away.** On the seven October
fixings 1.2.0 changes, 1.1.0 printed $3.88-$4.10 and 1.2.0 prints
$3.24-$3.35, which is the "continuing rate cards" level FINDINGS #13
measured by hand ($3.24-$3.36). The whole move is the floor fix: on each of
those days the US H100 rows Vast.ai actually priced came from one to three
machines, so its vote is dropped, the panel MAD falls back, and Paperspace's
unchanged $7.48 is screened again as it was through 30 September. The region
fix does nothing on those dates because the Australian and Russian rows had
already left the book. 5 October is the exception that shows the floor is
not simply removing Vast.ai: that day its US book priced 16 rows across
enough machines and hosts, its vote stands, and the fixing ($3.637) is the
same under both versions. 30 September is the same case.

**The two fixes interact, and the combination is not the sum.** On
17 September the region fix alone would have *raised* `GIX-H100` 29%: with
the cheap Australian and Russian rows gone, the US book that remained was
thin and expensive. Counted on the rows it actually priced, that book is
below the floor, so 1.2.0 drops Vast.ai's vote and lands at +4.8%. A region
screen that is correct is what exposes how thin the book underneath it was.

**The region fix moves less under 1.2.0 than FINDINGS #13 reported, and that
is not a contradiction.** The +32% (`GIX-H100`, 10 September) and +10%
(`GIX-B200`, 14 September) in #13 were measured under 1.0.0's rules, which
had no book floor. Under 1.1.0 and 1.2.0 those dates hold Vast.ai out
entirely for want of machine ids, so there is no Vast.ai vote for the region
screen to change. The defect was real and was in the 1.0.0 prints; it is not
in any 1.2.0 one.

**`GIX-A100` gains a fixing.** On 17 September Vast.ai's A100 vote rested on
too few priced machines; without it the panel's dispersion clears the gate
and the index publishes at $2.333 instead of withholding. The floor removes
a vote that was widening the panel, and here that is the difference between
a print and a gap.

**`GIX-B200` and `GIX-MI300X` do not move on any 1.1.0 date.**

## What 1.2.0 writes that 1.1.0 did not

A tape row from 1.2.0 onward carries a `venue_holdouts` cell naming every
marketplace whose vote the floor dropped from that index, with the priced
machine and host counts that failed it. `verify` recomputes that cell from
the archive along with the value. Rows written before the column existed
have it blank, because they never recorded it; the column was appended, so
no earlier value, date or reason was touched when the header was widened.
