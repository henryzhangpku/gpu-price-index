# The back-series: reconstructed from archived public rate cards

> **Reconstructed from archived public rate cards; list prices, not
> transactions; not the index.** Every row of every file this produces carries
> that sentence in its `label` column, because the files will be read without
> this page.

The live index starts on 27 August 2026. A research question that asks
whether GPU rental prices lead anything needs a longer series than that, so
this reconstructs one for January 2023 to September 2026 from the only public
record of what providers charged before the tape existed: the Internet
Archive's captures of their price pages.

It is a separate product with its own files, and it is built so that it
cannot leak into the index:

| | the index | the back-series |
|---|---|---|
| file | `series/index_values.csv` | `series/backfill_ratecards.csv` (monthly), `series/backfill_ratecards_weekly.csv` |
| inputs | live feeds, aggregators, an executable marketplace | archived HTML of eleven providers' price pages |
| unit of evidence | an offer or rate card seen today | a rate card as the archive happened to capture it |
| estimator | tier-weighted mean of provider medians, MAD screen, publication gates | median of provider medians, withheld below three providers |
| command | `gpuidx publish` | `gpuidx backfill` |

The backfill imports the index's normaliser and nothing writes in the other
direction; `tests/test_backfill_artifacts.py` asserts the build never emits
`index_values.csv`.

```bash
uv run gpuidx backfill            # rebuild every backfill file from the committed cache; no network
uv run gpuidx backfill --splice   # ... and compare the overlap with the live index
uv run gpuidx backfill --collect  # fetch captures not yet cached (polite, resumable), then rebuild
```

## 1. Sources

| source | archived URLs read | snapshots | months with an on-demand quote (H100 / H200 / B200 / A100) | first - last | what the parser reads |
|---|---|---|---|---|---|
| Lambda (Lambda Labs) | `lambdalabs.com/service/gpu-cloud`<br>`lambdalabs.com/service/gpu-cloud/pricing`<br>`lambda.ai/service/gpu-cloud`<br>`lambda.ai/pricing` | 105 | 38 / 0 / 14 / 42 | 2023-01 - 2026-09 | Instance prices to 2024, per-GPU from late 2024, a per-GPU column from mid-2025, a tab strip carrying the GPU count from Dec 2025. Reserved and cluster tables recorded as reserved or not read. |
| RunPod | `runpod.io/gpu-instance/pricing`<br>`runpod.io/pricing` | 89 | 37 / 23 / 18 / 39 | 2023-02 - 2026-09 | Embedded GraphQL GpuType records (2023-25), row data attributes (mid-2025 on), schema.org offers (2026). Secure Cloud is on-demand; Community Cloud and spot go to side columns. |
| CoreWeave | `coreweave.com/gpu-cloud-pricing`<br>`coreweave.com/pricing` | 40 | 21 / 21 / 16 / 17 | 2025-01 - 2026-09 | 2023-24 a-la-carte GPU component prices, discarded; from 2025 the on-demand instance table (8x HGX H100 $49.24/h). One snapshot a month. |
| Paperspace (DigitalOcean) | `paperspace.com/pricing` | 51 | 25 / 0 / 0 / 7 | 2023-05 - 2026-09 | Hourly cards plus footnotes; the H100 on-demand rate is stated only in a footnote. A template remnant on the H100 card is refused. |
| FluidStack | `fluidstack.io/pricing` | 19 | 8 / 0 / 0 / 9 | 2023-01 - 2025-04 | 2023 GPU component prices (host billed separately), discarded; 2024-25 per-GPU table; from mid-2025 cluster prices under no commitment heading, not read. |
| DataCrunch | `datacrunch.io/products` | 13 | 12 / 6 / 2 / 12 | 2023-09 - 2025-08 | Per-instance table keyed by instance name; term and spot columns go to side columns; an unlabelled Dynamic price column holds its place. Page last archived Aug 2025. |
| Hyperstack | `hyperstack.cloud/gpu-pricing` | 34 | 22 / 12 / 3 / 22 | 2024-02 - 2026-09 | Per-GPU tables under section headings; mid-2024 to mid-2025 a two-column table read as on-demand plus reservation floor (section 3). Late-2023 add-on host pricing makes that figure a component. |
| Nebius | `nebius.com/prices` | 28 | 16 / 16 / 11 / 0 | 2024-11 - 2026-09 | Four layouts (section 3); July 2025 to March 2026 the table header names no commitment and is read as on-demand. |
| DigitalOcean GPU Droplets | `digitalocean.com/pricing/gpu-droplets` | 27 | 18 / 12 / 0 / 0 | 2024-10 - 2026-09 | Droplet cards labelled on-demand, 12-month reserved or spot; promotional rates recorded as charged. |
| Crusoe Cloud | `crusoecloud.com/pricing`<br>`crusoe.ai/cloud/pricing` | 19 | 9 / 9 / 0 / 11 | 2024-05 - 2026-09 | A column strip (on-demand, spot, 6-month to 3-year reserved) matched slot by slot; H100 first priced May 2025. |
| Voltage Park | `voltagepark.com/pricing` | 15 | 9 / 0 / 0 / 0 | 2025-07 - 2026-06 | Offer cards stating fabric (Ethernet, InfiniBand) and the smallest rentable count; Contact for pricing from July 2026. |

**Candidates read and not used**, each for a reason found in the captures
themselves rather than assumed:

| page | capture probed | why it is not used |
|---|---|---|
| AWS `aws.amazon.com/ec2/instance-types/p5/` | Jun 2024 | no dollar figure anywhere in the archived HTML |
| Google Cloud `cloud.google.com/compute/gpus-pricing` | Jun 2024 | the H100 price is not on this page, which refers to the machine-type pricing page; that page was not parsed |
| Azure `azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/` | Jun 2024 | no H100 row in the archived HTML |
| Oracle `oracle.com/cloud/price-list/` | Jun 2024 | `BM.GPU.H100.8` is listed with its "GPU Price Per Hour" cell empty |
| Vultr `vultr.com/pricing/` | Jun 2024 | the H100 figure is a 36-month prepaid reservation ("Starting from"), not an on-demand rate |
| Together `together.ai/pricing` | Jun 2024 | per-minute hosting of fine-tuned model endpoints, not a GPU rental |
| Vast.ai `vast.ai/pricing`, TensorDock, Jarvislabs, Ori, Massed Compute | 2024-25 | no H100 price in the archived HTML of the capture probed |
| Latitude.sh, Cudo Compute | Jun 2024 | only "from $X" teasers (Cudo's for reserved capacity), which the live screen rejects |
| Thunder Compute | Jun 2025 | network-attached virtualised GPUs with 4 vCPUs, a different good from the contract |

The list is the population. A provider whose page was never archived, was
archived only as a script shell, or quoted only "contact sales" is absent,
and the series cannot say what it charged.

## 2. Collection

`src/gpuidx/backfill/wayback.py` is the only code in the backfill that opens a
socket.

* **Listings.** The Wayback timemap endpoint, one request per URL for
  2023-2026, falling back to the CDX endpoint a year at a time. The CDX
  server's own `filter` and `collapse` parameters returned HTTP 503 for every
  query tried during development, so status filtering and period selection
  are done client-side. Each listing is cached in `backfill/cdx/<source>.json`;
  selection is a function of that file, so it reproduces.
* **Usable captures** are those whose own CDX record is an archived 200 HTML
  response. `warc/revisit` records are dropped: requesting one makes the
  archive serve some other capture.
* **The timestamp rule.** A snapshot is fetched with the `id_` flag (the bytes
  as captured, no toolbar) and **only ever filed under the timestamp the
  archive actually served**. The archive often answers a request for one
  capture with a redirect to a neighbouring one -- 18 April 2023 was served
  as 20 May 2023 on one check -- and on a full re-check of the first two
  sources collected, 29 of 121 Lambda and 16 of 98 RunPod captures that the
  CDX listed as 200 were redirected elsewhere. Those were deleted and are
  never read; the fetcher now refuses any redirect that changes the
  timestamp and moves on to the next capture in the period.
* **Selection.** One snapshot per ISO week per source (CoreWeave, whose page
  is large and archived daily, one per month), trying up to three captures in
  the period before the period is left empty.
* **Politeness.** At least 1.5 s between requests, short retry ladders on 429
  and 5xx, a descriptive User-Agent, and every fetched body cached, so a
  second run makes no requests for what it already has.
* **The cache is committed.** `backfill/archive/<source>/<timestamp>.html.gz`
  (gzip with a zero mtime, so the bytes are a function of the content), and
  `backfill/manifest.csv` lists every snapshot read with its capture URL, CDX
  digest, the SHA-256 of the cached body, its size, and how many rows the
  parser read from it. The committed cache is 440 snapshots, 10.8 MB gzipped (76 MB of HTML), well under the 50 MB at which a manifest of URLs and hashes would have been committed instead.

## 3. Reading a page

One small parser per provider in `src/gpuidx/backfill/parsers/`, each working
on the page's visible text reduced to cells in reading order, plus embedded
records where a page carries its price list as data (RunPod). Their docstrings
describe every layout met and the rule used for it. The rules that apply to
all of them:

* **Only what the page states.** A model string is mapped onto the live
  contract aliases (`H100 SXM`, `H100 PCIe`, `A100 SXM 40GB`, ...). Form
  factor and fabric are recorded only where the page names them ("SXM",
  "HGX", "PCIe", "InfiniBand", "Ethernet", "NVLink"); otherwise they are
  `unknown` and the live schedule prices that uncertainty. Nothing is
  inferred from price.
* **Commitment comes from the page's own words** -- a column header, a card
  heading, or the section heading a table sits under. A price column whose
  header and section say nothing is not read. Two pages need a stated
  reading, disclosed in their parser and here: Hyperstack's mid-2024 to
  mid-2025 table, whose "Pricing Per Hour" column stands beside "Reservation
  Pricing" and is read as on-demand; and Nebius's July 2025 to March 2026
  "Price per GPU-hour" column, read as on-demand because it is the page's
  only self-serve table and is headed "On-demand, GPU-hour", with identical
  figures, from April 2026.
* **Placeholders hold their column.** "n/a", "Contact sales", "On request" and
  dashes occupy a price slot, so a missing on-demand price cannot shift a
  reserved price into the on-demand column (FluidStack's H200, January 2025).
* **Neighbouring products are refused or labelled.** GH200, GB200, B300 and
  H800 are not read as H200/B200/H100. H100 NVL and 40 GB A100s are read and
  labelled, so the live product-identity screen rejects them with its reason.
* **Template remnants and footnotes.** Paperspace's H100 card carried a price
  block copied from its A100 card; a price is only read where the spec line
  under it names the card's own GPU. Asterisked prices are read only where a
  footnote states their commitment, and the footnote sentence "On-demand
  pricing for H100 is $X/hour" is read as the on-demand quote it is.
* **Per-GPU pages are one GPU.** Where a page prices per GPU-hour and states
  no node size, a row is one GPU, the live RunPod adapter's convention, and
  the node-size factor applies.
* **Duplicates collapse.** A page that repeats a table for its mobile layout
  states each price once per snapshot.

Parsers are tested on verbatim excerpts of real captures in
`tests/fixtures/backfill/`, each labelled at its head with the capture URL,
timestamp, CDX digest and the markers it was cut between.

## 4. Restating to the standard good

`src/gpuidx/backfill/restate.py` adds no adjustment rule of its own. Each row
becomes the live `RawObservation` (tier 2, region undisclosed) and goes
through the live `normalize` under **methodology 1.1.0**, pinned so the
committed CSV rebuilds after the live index moves on. That applies the
form-factor, interconnect and node-size factors of METHODOLOGY section 4, the
1.75x cumulative cap, the product-identity screen and the "from $X" teaser
screen exactly as the index does. Before it, three exclusions:

| discarded | why |
|---|---|
| `not_on_demand` | reserved, spot and community prices are kept, unrestated, in side columns (`reserved_raw_median`, `spot_raw_median`); restating a 3-year term by the 1.25 factor would put the schedule's judgement at the centre of a series whose only claim is that it reads published prices |
| `component_price` | CoreWeave's a-la-carte card (2023-24) and FluidStack's 2023 table price the GPU alone and bill vCPUs and RAM on top; the contract includes the host, and completing the price would mean choosing a host the page never quoted |
| `out_of_scope` | a contract the backfill does not cover (MI300X) |

and the live rejections (`product_identity`, `from_floor`, `over_adjusted`).
Every row read, kept or discarded, is in `backfill/observations.csv` with its
snapshot, its restated price and adjustments or its discard code and reason.

Rows read per index, and what became of them:

| index | restated | not_on_demand | product_identity | component_price | from_floor | over_adjusted |
|---|---|---|---|---|---|---|
| `GIX-H100` | 914 | 823 | 72 | 40 | 0 | 0 |
| `GIX-H200` | 204 | 177 | 0 | 0 | 0 | 0 |
| `GIX-B200` | 226 | 73 | 0 | 0 | 0 | 0 |
| `GIX-A100` | 516 | 820 | 585 | 84 | 0 | 0 |

## 5. Aggregation

Per index and per period (calendar month; ISO week, named by its Monday):

1. **One vote per provider**: the median of its restated on-demand quotes
   across every snapshot of its page in the period.
2. **The value is the median of the votes**, unweighted. Every input is the
   same kind of evidence, so there is no tier to weight by, and on three to
   eight voters a weighted mean is one provider's opinion on the day another
   is missing.
3. **Withheld below three providers**, with the row still written and the
   reason recorded -- the same refusal the live gates make, at a floor that
   suits a panel that cannot be widened.
4. **Dispersion** (robust CV of the votes) is reported and flagged above the
   live 0.45 ceiling, never gated: on three to five points it is too coarse to
   refuse on.
5. **`matched_log_change`**: the median, over providers voting in both this
   period and the previous one, of the log change of each one's own vote,
   published when at least three match. The level moves when the panel
   changes; this does not. It is a second estimator, not a fill -- no level
   is ever carried across a gap.

### Coverage, provider x month

One character per month from January 2023 to September 2026: `.` no snapshot read, `o` snapshots read but no on-demand quote for a covered GPU, a digit the number of the four GPUs (H100, H200, B200, A100) with at least one restated on-demand quote that month. The same table with counts is `backfill/coverage.csv`.

```
                2023        2024        2025        2026     
                |           |           |           |        
lambda          11112.22222222222.2222222222.2233333333333333
runpod          .1.1...222222.2222222233333444444444444444444
coreweave       o.oo..o.o.oooooooooooooo222234444444444444444
paperspace      ....1.1.1111211111111.11111...111..111111..11
fluidstack      1.o.....o...22..22..2...22.2.oo..o...........
datacrunch      ........2....2.2.222..3333.4...4.............
hyperstack      ..........o..2.2..22.22222..2.3333.333.3.3444
nebius          ......................222.2..233333o.333..333
digitalocean    .....................1.1111...12222.2.2222222
crusoe          ....o...........1....1......33..3..3.3...3333
voltagepark     ..............................1.111.11.111ooo
```

## 6. The series

Monthly value in USD per GPU-hour of the standard good, with the number of providers voting in brackets; `--` is withheld (fewer than three). The provider votes, dispersion, matched change and side columns behind every cell are in `series/backfill_ratecards.csv`.

* `GIX-H100`: 34 of 45 months published, 2023-09 to 2026-09.
* `GIX-H200`: 23 of 45 months published, 2024-11 to 2026-09.
* `GIX-B200`: 15 of 45 months published, 2025-07 to 2026-09.
* `GIX-A100`: 35 of 45 months published, 2023-09 to 2026-09.

| month | H100 | H200 | B200 | A100 |
|---|---|---|---|---|
| 2023-01 | -- (0) | -- (0) | -- (0) | -- (2) |
| 2023-02 | -- (0) | -- (0) | -- (0) | -- (2) |
| 2023-03 | -- (0) | -- (0) | -- (0) | -- (1) |
| 2023-04 | -- (0) | -- (0) | -- (0) | -- (2) |
| 2023-05 | -- (1) | -- (0) | -- (0) | -- (2) |
| 2023-06 | -- (0) | -- (0) | -- (0) | -- (0) |
| 2023-07 | -- (1) | -- (0) | -- (0) | -- (2) |
| 2023-08 | -- (2) | -- (0) | -- (0) | -- (2) |
| 2023-09 | 3.38 (3) | -- (0) | -- (0) | 2.19 (4) |
| 2023-10 | -- (2) | -- (0) | -- (0) | 2.25 (3) |
| 2023-11 | -- (2) | -- (0) | -- (0) | 2.30 (3) |
| 2023-12 | -- (2) | -- (0) | -- (0) | 2.30 (3) |
| 2024-01 | 4.78 (4) | -- (0) | -- (0) | 2.34 (4) |
| 2024-02 | 4.84 (5) | -- (0) | -- (0) | 2.35 (4) |
| 2024-03 | 4.59 (3) | -- (0) | -- (0) | -- (2) |
| 2024-04 | 4.59 (5) | -- (0) | -- (0) | 2.06 (4) |
| 2024-05 | 4.33 (4) | -- (0) | -- (0) | 1.92 (4) |
| 2024-06 | 4.26 (4) | -- (0) | -- (0) | 1.91 (3) |
| 2024-07 | 3.84 (5) | -- (0) | -- (0) | 1.87 (4) |
| 2024-08 | 3.84 (5) | -- (0) | -- (0) | 1.87 (4) |
| 2024-09 | 3.51 (4) | -- (0) | -- (0) | 1.89 (3) |
| 2024-10 | 3.04 (4) | -- (0) | -- (0) | 1.84 (4) |
| 2024-11 | 2.93 (6) | 3.46 (3) | -- (0) | 1.70 (4) |
| 2024-12 | 3.03 (7) | 3.36 (3) | -- (0) | 1.70 (4) |
| 2025-01 | 3.03 (9) | 3.63 (4) | -- (0) | 1.79 (5) |
| 2025-02 | 3.15 (8) | 3.67 (3) | -- (0) | 1.79 (5) |
| 2025-03 | 3.33 (6) | 3.72 (3) | -- (0) | -- (2) |
| 2025-04 | 3.03 (5) | 3.67 (3) | -- (2) | 1.84 (4) |
| 2025-05 | 3.23 (4) | 3.95 (3) | -- (1) | 1.91 (4) |
| 2025-06 | 3.14 (5) | 3.83 (4) | -- (2) | 1.91 (4) |
| 2025-07 | 2.89 (8) | 3.73 (4) | 6.37 (3) | 1.81 (4) |
| 2025-08 | 2.88 (8) | 3.52 (6) | 5.06 (5) | 1.79 (5) |
| 2025-09 | 3.03 (9) | 3.88 (6) | 5.71 (4) | 1.82 (5) |
| 2025-10 | 2.73 (7) | 3.82 (5) | 5.55 (4) | 1.81 (4) |
| 2025-11 | 2.88 (6) | 3.74 (4) | 5.45 (4) | 1.79 (3) |
| 2025-12 | 3.31 (6) | 3.88 (4) | 5.52 (3) | 1.79 (5) |
| 2026-01 | 3.03 (7) | 3.52 (4) | 5.41 (3) | 1.67 (4) |
| 2026-02 | 3.10 (8) | 3.82 (5) | 5.46 (4) | 1.79 (5) |
| 2026-03 | 3.30 (6) | 3.52 (4) | 5.50 (4) | 2.06 (3) |
| 2026-04 | 2.99 (8) | 3.22 (5) | 6.22 (4) | 2.17 (4) |
| 2026-05 | 3.55 (6) | 4.24 (3) | 6.60 (3) | 2.79 (3) |
| 2026-06 | 3.32 (7) | 3.95 (5) | 6.60 (3) | 2.31 (5) |
| 2026-07 | 3.54 (7) | 4.04 (6) | 6.58 (5) | 2.31 (5) |
| 2026-08 | 3.79 (8) | 4.13 (6) | 6.60 (5) | 2.31 (5) |
| 2026-09 | 3.79 (8) | 4.13 (6) | 6.60 (5) | 2.31 (5) |

## 7. Against the live index

The live tape starts on 27 August 2026 and the backfill reads captures
through September, so the two overlap for five weeks. `gpuidx backfill
--splice` writes the comparison to `backfill/splice_level.csv` and
`backfill/splice_providers.csv`. **Nothing is spliced.**

**Level.** Each overlap period's backfill value against the mean of the live
fixings published in it:

| period | index | backfill | providers | live mean | live days | live / backfill |
|---|---|---|---|---|---|---|
| month 2026-08 | `GIX-H100` | 3.7890 | 8 | 3.3040 | 5 | 0.8720 |
| month 2026-08 | `GIX-H200` | 4.1262 | 6 | 3.6631 | 5 | 0.8878 |
| month 2026-08 | `GIX-B200` | 6.5998 | 5 | 5.9169 | 5 | 0.8965 |
| month 2026-08 | `GIX-A100` | 2.3064 | 5 | 1.6695 | 1 | 0.7239 |
| month 2026-09 | `GIX-H100` | 3.7890 | 8 | 3.1343 | 20 | 0.8272 |
| month 2026-09 | `GIX-H200` | 4.1262 | 6 | 3.5713 | 20 | 0.8655 |
| month 2026-09 | `GIX-B200` | 6.5998 | 5 | 6.1186 | 20 | 0.9271 |
| month 2026-09 | `GIX-A100` | 2.3064 | 5 | 1.8600 | 5 | 0.8065 |
| week 2026-08-24 | `GIX-H100` | 3.5880 | 5 | 3.3503 | 4 | 0.9338 |
| week 2026-08-24 | `GIX-H200` | 4.0296 | 4 | 3.6345 | 4 | 0.9019 |
| week 2026-08-24 | `GIX-B200` | 6.5998 | 3 | 5.8984 | 4 | 0.8937 |
| week 2026-08-24 | `GIX-A100` | 1.9528 | 4 | 1.6695 | 1 | 0.8549 |
| week 2026-08-31 | `GIX-H100` | 3.5650 | 6 | 3.1195 | 7 | 0.8750 |
| week 2026-08-31 | `GIX-H200` | 4.1400 | 5 | 3.7717 | 7 | 0.9110 |
| week 2026-08-31 | `GIX-B200` | 6.5998 | 5 | 5.8613 | 7 | 0.8881 |
| week 2026-08-31 | `GIX-A100` | 2.3064 | 5 | 1.8841 | 3 | 0.8169 |
| week 2026-09-07 | `GIX-H100` | 3.4757 | 4 | 3.0801 | 7 | 0.8862 |
| week 2026-09-07 | `GIX-H200` | 4.1400 | 3 | 3.5200 | 7 | 0.8502 |
| week 2026-09-07 | `GIX-B200` | 6.5889 | 4 | 6.1822 | 7 | 0.9383 |
| week 2026-09-07 | `GIX-A100` | 1.7239 | 3 | 1.9464 | 1 | 1.1291 |
| week 2026-09-14 | `GIX-H100` | 3.5650 | 6 | 3.1615 | 4 | 0.8868 |
| week 2026-09-14 | `GIX-H200` | 4.1124 | 5 | 3.3832 | 4 | 0.8227 |
| week 2026-09-14 | `GIX-B200` | 6.5889 | 4 | 6.1191 | 4 | 0.9287 |
| week 2026-09-14 | `GIX-A100` | 2.0152 | 4 | 1.7015 | 1 | 0.8443 |
| week 2026-09-28 | `GIX-H100` | 3.4987 | 4 | 3.6652 | 7 | 1.0476 |
| week 2026-09-28 | `GIX-H200` | 3.9468 | 3 | 3.5620 | 7 | 0.9025 |
| week 2026-09-28 | `GIX-B200` | 6.5998 | 3 | 6.2469 | 7 | 0.9465 |

The live index sits **7-17% below the back-series** for H100, H200 and B200:
0.87 and 0.83 for H100 in August and September, 0.89 and 0.87 for H200,
0.90 and 0.93 for B200. The A100 rows rest on one to five live days,
because the live A100 index is withheld most days, and should not be read.

**It is not stable enough to splice on.** Week by week the H100 ratio runs
0.93, 0.88, 0.89, 0.89 and then 1.05 in the week of 28 September, whose live
days run to 4 October and take in the start of the live index's
early-October jump (FINDINGS #13); the week of 21 September has no live
fixings at all. A constant fitted to these weeks would be
fitted to a gap that changed by more than its own size inside them, and to a
live series that, by FINDINGS #13, was itself moving on composition rather
than price.

The gap has a plain cause: **the two panels are different sellers.** The live
H100 fixing draws on Vast.ai's marketplace and on Shadeform-routed neoclouds
quoting $1.95-$2.50 -- Voltage Park, whose archived page stopped printing a
price in July 2026, and Latitude and Denvr, which are not in the backfill's
panel -- and weights them by tier; the back-series carries CoreWeave
($6.16) and Paperspace ($5.47), whose cards the archive kept, and takes a
plain median. It is a statement about which prices each method can see, not
a measured premium of list over market.

**Like for like.** The same seller read both ways -- its archived page
against its live feed over the same days -- is the check on the parsers:

| month | index | backfill source | live provider | backfill vote | live median | live / backfill |
|---|---|---|---|---|---|---|
| 2026-08 | `GIX-A100` | crusoe | `shadeform:crusoe` | 2.3064 | 2.0599 | 0.8931 |
| 2026-08 | `GIX-A100` | runpod | `runpod` | 1.5991 | 1.6989 | 1.0624 |
| 2026-08 | `GIX-B200` | runpod | `runpod` | 7.2176 | 7.7405 | 1.0725 |
| 2026-08 | `GIX-H100` | digitalocean | `shadeform:digitalocean` | 4.0572 | 4.0572 | 1.0000 |
| 2026-08 | `GIX-H100` | hyperstack | `shadeform:hyperstack` | 2.6574 | 3.2000 | 1.2042 |
| 2026-08 | `GIX-H100` | runpod | `runpod` | 3.3174 | 3.2235 | 0.9717 |
| 2026-08 | `GIX-H200` | digitalocean | `shadeform:digitalocean` | 4.1124 | 4.2912 | 1.0435 |
| 2026-08 | `GIX-H200` | runpod | `runpod` | 4.8790 | 4.1686 | 0.8544 |
| 2026-09 | `GIX-A100` | crusoe | `shadeform:crusoe` | 2.3064 | 2.0599 | 0.8931 |
| 2026-09 | `GIX-A100` | lambda | `shadeform:lambdalabs` | 2.7900 | 2.7452 | 0.9839 |
| 2026-09 | `GIX-A100` | runpod | `runpod` | 1.7239 | 1.7969 | 1.0423 |
| 2026-09 | `GIX-B200` | lambda | `shadeform:lambdalabs` | 6.5998 | 6.4308 | 0.9744 |
| 2026-09 | `GIX-B200` | runpod | `runpod` | 7.2176 | 7.7405 | 1.0725 |
| 2026-09 | `GIX-H100` | digitalocean | `shadeform:digitalocean` | 4.0572 | 4.0572 | 1.0000 |
| 2026-09 | `GIX-H100` | hyperstack | `shadeform:hyperstack` | 2.6574 | 3.2000 | 1.2042 |
| 2026-09 | `GIX-H100` | lambda | `shadeform:lambdalabs` | 3.9900 | 4.0097 | 1.0049 |
| 2026-09 | `GIX-H100` | paperspace | `shadeform:paperspace` | 5.4740 | 7.4782 | 1.3661 |
| 2026-09 | `GIX-H100` | runpod | `runpod` | 3.4094 | 3.2235 | 0.9455 |
| 2026-09 | `GIX-H200` | digitalocean | `shadeform:digitalocean` | 4.1124 | 4.2912 | 1.0435 |
| 2026-09 | `GIX-H200` | runpod | `runpod` | 4.8790 | 4.1686 | 0.8544 |

Where the configuration matches, the two reads agree: DigitalOcean H100 to
the cent (1.000), Lambda H100 1.005, Lambda A100 0.98 and B200 0.97, RunPod
H100 0.95-0.97. The large gaps are configuration, not error. Hyperstack's
backfill vote (1.20) is the median of every H100 it lists, PCIe and NVLink
cards included, where the live feed's Hyperstack quote is its SXM node at
$3.20, which the archived page also prints at $3.20; Paperspace (1.37)
is read from the page's own on-demand footnote, $5.95, against the $7.48 the
Shadeform feed quotes for the same provider; RunPod H200 (0.85) is a card
whose page says only "H200", restated with the unknown-form-factor factors,
against the live feed that names it SXM.


## 8. Known biases

* **List prices lag deals.** A rate card is the price a provider was willing
  to print. Negotiated, volume and committed prices -- most of the market by
  volume -- sit below it and move first. The series is plausibly late at
  turning points and high in level.
* **Survivorship of the archived.** The panel is the providers whose pages
  the Internet Archive kept, rendered prices into HTML, and quoted a number.
  It omits AWS, Google Cloud, Azure and Oracle, whose archived pages read carry no
  usable price; marketplaces (Vast.ai, TensorDock) whose archived pages show
  no H100 price; and every provider that only says "contact sales". It leans towards
  small and mid-sized neoclouds that publish a card.
* **A marketplace is not here at all.** The live index's only executable
  source is a marketplace book; the backfill has none. The live index has
  seen Vast.ai's H100 book median range from $1.77 to $9.75 within ten days
  (FINDINGS #13); nothing in this series moves like that, which is a property of rate cards, not of the
  market.
* **Capture density varies.** A provider is in a month's panel only if the
  archive captured its page that month and the capture was served as itself.
  Panels therefore change with archive behaviour, which is why the matched
  change exists and why the level should be read with its provider list.
* **Restatement inherits the schedule.** Most inputs are adjusted (PCIe and
  single-GPU rows especially); the adjustment exposure the index discloses
  (METHODOLOGY section 4) applies here too.
* **Promotions are prices.** Where a page shows a struck-through list price
  and a promotional rate, the promotional rate is recorded, because that is
  what the page offered on demand.

## 9. What it must not be used for

* **Not as the index, and not spliced onto it.** Section 7 measures the gap;
  it does not license a splice.
* **Not for settlement, valuation or any claim about transaction prices.**
* **Not week by week as if each week were observed.** The weekly file exists
  for density, not precision: most weeks have fewer than three providers.
* **Not as evidence about providers absent from it**, including every
  hyperscaler.
* **Not as a level comparison between GPUs across the period's early part**,
  where one or two providers set an index alone and the row is withheld.

In a lead-lag study it is a monthly, list-price, survivor-panel proxy, and the
honest use is with those words attached: test whether its matched changes
lead, check that the result survives dropping each provider in turn, and treat
anything that rests on one provider's repricing as that provider's decision,
not the market's.

## 10. Extending it

Add a `Source` in `src/gpuidx/backfill/sources.py`, a parser registered in
`src/gpuidx/backfill/parsers/`, and a fixture cut from a real capture; run
`gpuidx backfill --collect --source <name>`, commit the cache, the CSVs and
the fixture together. A change to a parser changes committed values, which
the reproducibility test catches until the CSVs are rebuilt and committed in
the same change.
