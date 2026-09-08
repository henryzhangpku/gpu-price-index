"""Operator interface.

The commands map to what a benchmark administrator actually does: run the
fixing (``publish``), read the tape (``show``), audit how one number was built
(``audit``), answer an as-of question (``as-of``, ``revisions``), restate the
rules (``contracts``), rebuild and prove the series from its archive
(``rebuild``, ``verify``), test the adjustment factors against venue pricing
(``calibrate``), and examine what committed-use discounts do and do not imply
about term structure (``forward``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .calibrate import compare_to_schedule, observed_ratios
from .forward import (
    annual_decline_pct,
    consistency_check,
    implied_decline,
    implied_forward_level,
    load_committed_use,
    premium_sensitivity,
)
from .pipeline import run_daily
from .reproduce import coverage, estimate_from_archive, rebuild, verify
from .sensitivity import exposure, exposure_all
from .spec import CONTRACTS, DEFAULT_GATES
from .store import Store

#: Repository root, where snapshots/ and series/ live.
ARCHIVE_ROOT = Path(__file__).resolve().parents[2]

app = typer.Typer(add_completion=False, help="GPU rental price benchmark reference implementation")
console = Console()

SEVERITY_STYLE = {"info": "dim", "warn": "yellow", "error": "bold red"}


def _store(db: Path | None) -> Store:
    return Store(db)


def _fmt(value: float | None) -> str:
    return f"${value:.3f}" if value is not None else "--"


@app.command()
def publish(
    db: Path | None = typer.Option(None, help="SQLite path"),
    index_date: str | None = typer.Option(None, "--date", help="Index date (YYYY-MM-DD)"),
    reason: str | None = typer.Option(None, help="Revision reason, if correcting"),
    archive: bool = typer.Option(
        True, help="Write an immutable snapshot and append to the tape"
    ),
) -> None:
    """Run one full collection and publication cycle."""
    store = _store(db)
    target = date.fromisoformat(index_date) if index_date else None

    with console.status("collecting from venues..."):
        report = run_daily(
            store,
            index_date=target,
            revision_reason=reason,
            archive_root=ARCHIVE_ROOT if archive else None,
        )

    console.print(
        Panel(
            f"run [bold]{report.run_id}[/] for [bold]{report.index_date}[/]\n"
            f"{report.raw_count} raw observations -> {report.quote_count} normalised quotes",
            title="collection",
            expand=False,
        )
    )

    table = Table(title="[bold]daily fixing[/]", title_justify="left",
                  box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("index")
    table.add_column("value", justify="right")
    table.add_column("prov", justify="right")
    table.add_column("obs", justify="right")
    table.add_column("disp", justify="right")
    table.add_column("status")
    for code, value in report.values.items():
        published = value.status.value == "published"
        table.add_row(
            code,
            _fmt(value.value),
            str(value.provider_count),
            str(value.observation_count),
            f"{value.dispersion:.3f}" if value.dispersion is not None else "--",
            "[green]published[/]" if published else f"[red]withheld[/] {value.withheld_reason or ''}",
        )
    console.print(table)

    if report.flags:
        console.print("\n[bold]quality flags[/]")
        for flag in report.flags:
            style = SEVERITY_STYLE.get(flag.severity, "")
            scope = flag.index_code or "run"
            console.print(f"  [{style}]{flag.severity:5}[/] [cyan]{scope:11}[/] {flag.code:26} {flag.detail}")
    store.close()


@app.command()
def show(
    index_code: str = typer.Argument(..., help="e.g. GIX-H100"),
    db: Path | None = typer.Option(None),
    limit: int = typer.Option(20),
) -> None:
    """Print the current view of a series."""
    store = _store(db)
    rows = store.history(index_code, limit)
    if not rows:
        console.print(f"[yellow]no values for {index_code}[/]")
        raise typer.Exit(1)

    # A one-line summary, not a panel. The full contract belongs in `contracts`;
    # printing it in a box above every series turned the thing you asked for
    # into the smallest object on screen.
    contract = CONTRACTS.get(index_code)
    subtitle = ""
    if contract:
        subtitle = (
            f"1x {contract.gpu_model} {contract.form_factor.value.upper()} "
            f"{contract.vram_gb}GB, {contract.commitment.value.replace('_', '-')}, "
            f"{contract.node_size}-GPU {contract.interconnect.value}, {contract.region}"
        )

    table = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold",
        pad_edge=False,
        title=f"[bold]{index_code}[/]" + (f"  ·  {contract.display_name}" if contract else ""),
        title_justify="left",
        caption=f"[dim]{subtitle}[/]" if subtitle else None,
        caption_justify="left",
    )
    for column in ("date", "value", "rev", "prov", "obs", "disp", "status"):
        table.add_column(column, justify="right" if column != "status" else "left", no_wrap=True)
    for row in rows:
        published = row["status"] == "published"
        table.add_row(
            row["index_date"],
            f"[bold cyan]{_fmt(row['value'])}[/]" if published else "[dim]--[/]",
            str(row["revision"]) if row["revision"] else "[dim]0[/]",
            str(row["provider_count"]),
            str(row["observation_count"]),
            f"{row['dispersion']:.3f}" if row["dispersion"] is not None else "[dim]--[/]",
            "[green]● published[/]" if published else "[yellow]○ withheld[/]",
        )
    console.print()
    console.print(table)
    console.print()
    store.close()


@app.command()
def audit(
    index_code: str = typer.Argument(...),
    index_date: str = typer.Argument(...),
    db: Path | None = typer.Option(None),
    revision: int | None = typer.Option(None, help="Defaults to the live revision"),
) -> None:
    """Show every provider behind one published value, screened ones included."""
    store = _store(db)
    target = date.fromisoformat(index_date)
    row = store.latest(index_code, target)
    if row is None:
        console.print(f"[yellow]no value for {index_code} on {index_date}[/]")
        raise typer.Exit(1)
    rev = revision if revision is not None else row["revision"]

    console.print(
        Panel(
            f"value {_fmt(row['value'])}  revision {rev}  status {row['status']}\n"
            f"methodology {row['methodology_version']}  published {row['published_at']}",
            title=f"{index_code} {index_date}",
            expand=False,
        )
    )

    table = Table(title="[bold]contributions[/]", title_justify="left",
                  box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("provider")
    table.add_column("price", justify="right")
    table.add_column("weight", justify="right")
    table.add_column("share", justify="right")
    table.add_column("quotes", justify="right")
    table.add_column("tier", justify="right")
    table.add_column("note")

    contributions = store.contributions(index_code, target, rev)
    provenance = "contributions recorded at publication"

    if not contributions:
        # A rebuilt store has no contributions: they are written by a live
        # publish, and `rebuild` restores the tape rather than replaying one.
        # That is the state every fresh clone is in, so fall back to deriving
        # the breakdown from the inputs the tape names. It is also the better
        # answer -- the table says what was computed once, the snapshot says
        # what the archived inputs still support.
        recomputed, detail = estimate_from_archive(ARCHIVE_ROOT, index_code, index_date, rev)
        if recomputed is None:
            console.print(f"[yellow]no contribution detail available: {detail}[/]")
            store.close()
            raise typer.Exit(1)
        total_weight = sum(p.weight for p in recomputed.contributing) or 1.0
        for agg in sorted(recomputed.providers, key=lambda a: (a.screened_out, -a.weight)):
            table.add_row(
                f"[strike]{agg.provider}[/]" if agg.screened_out else agg.provider,
                _fmt(agg.price),
                f"{agg.weight:.2f}",
                "--" if agg.screened_out else f"{agg.weight / total_weight:.1%}",
                str(agg.quote_count),
                str(int(agg.best_tier)),
                agg.screen_reason or ("screened" if agg.screened_out else ""),
            )
        console.print(table)
        console.print(f"[dim]recomputed from {detail}[/]")
        store.close()
        return

    total_weight = sum(c["weight"] for c in contributions if not c["screened_out"]) or 1.0
    for contribution in contributions:
        screened = bool(contribution["screened_out"])
        table.add_row(
            f"[strike]{contribution['provider']}[/]" if screened else contribution["provider"],
            _fmt(contribution["price"]),
            f"{contribution['weight']:.2f}",
            "--" if screened else f"{contribution['weight'] / total_weight:.1%}",
            str(contribution["quote_count"]),
            str(contribution["tier"]),
            contribution["screen_reason"] or ("screened" if screened else ""),
        )
    console.print(table)
    console.print(f"[dim]{provenance}[/]")
    store.close()


@app.command("as-of")
def as_of(
    index_code: str = typer.Argument(...),
    index_date: str = typer.Argument(..., help="Index date (event time)"),
    knowledge_time: str = typer.Argument(..., help="ISO timestamp (knowledge time)"),
    db: Path | None = typer.Option(None),
) -> None:
    """Answer: what did we say for this date, as known at that moment?"""
    store = _store(db)
    when = datetime.fromisoformat(knowledge_time)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)

    row = store.as_of(index_code, date.fromisoformat(index_date), when)
    if row is None:
        console.print(
            f"[yellow]{index_code} for {index_date} was not yet published as of {when.isoformat()}[/]"
        )
        raise typer.Exit(1)

    console.print(
        Panel(
            f"value      {_fmt(row['value'])}\n"
            f"status     {row['status']}\n"
            f"revision   {row['revision']}\n"
            f"published  {row['published_at']}\n"
            f"superseded {row['superseded_at'] or 'no -- this was still the live value'}",
            title=f"{index_code} {index_date} as known at {when.isoformat()}",
            expand=False,
        )
    )
    store.close()


@app.command()
def revisions(
    index_code: str = typer.Argument(...),
    index_date: str = typer.Argument(...),
    db: Path | None = typer.Option(None),
) -> None:
    """List every revision of one value, including superseded ones."""
    store = _store(db)
    rows = store.revisions(index_code, date.fromisoformat(index_date))
    if not rows:
        console.print("[yellow]no revisions[/]")
        raise typer.Exit(1)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("rev", "value", "status", "published_at", "superseded_at", "reason"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["revision"]),
            _fmt(row["value"]),
            row["status"],
            row["published_at"],
            row["superseded_at"] or "[green]live[/]",
            row["revision_reason"] or "",
        )
    console.print(table)
    store.close()


@app.command()
def contracts() -> None:
    """Print the benchmark contracts, the gates, and the constraints.

    Gates and constraints are listed separately on purpose. A gate can fail and
    withhold the value. The other two never can -- the weight share is enforced
    by capping, and the level-shift threshold flags for sign-off rather than
    blocking. Printing all five under one heading claims a stronger guarantee
    than the code provides. METHODOLOGY section 7 draws the same line.
    """
    # One row per contract rather than five full-width panels. The panels made
    # you scroll past the definitions to reach the three numbers most people
    # open this command for.
    defs = Table(title="[bold]benchmark-equivalent contracts[/]", title_justify="left",
                 box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False,
                 caption="[dim]every input is restated as one of these, or discarded[/]",
                 caption_justify="left")
    for column in ("index", "GPU", "form", "VRAM", "node", "fabric", "region"):
        defs.add_column(column, justify="right" if column in ("VRAM", "node") else "left",
                        no_wrap=True)
    for contract in CONTRACTS.values():
        defs.add_row(
            f"[bold]{contract.index_code}[/]",
            contract.gpu_model,
            contract.form_factor.value.upper(),
            f"{contract.vram_gb}GB",
            str(contract.node_size),
            contract.interconnect.value,
            contract.region,
        )
    console.print()
    console.print(defs)
    console.print(
        "[dim]  priced in USD per GPU-hour, dedicated and non-preemptible. Excludes "
        "persistent storage,\n  egress, support tiers, and any committed-use or credit "
        "discount.[/]\n"
    )
    gates = DEFAULT_GATES
    console.print(
        Panel(
            f"min providers          {gates.min_providers}\n"
            f"min observations       {gates.min_observations}\n"
            f"max dispersion         {gates.max_dispersion}",
            title="publication gates - failing any of these withholds",
            expand=False,
        )
    )
    console.print(
        Panel(
            f"max provider weight    {gates.max_provider_weight_share:.0%}"
            "   capped iteratively, so it cannot fail\n"
            f"review move threshold  {gates.review_move_threshold:.0%}"
            "   flags for sign-off, does not block\n"
            f"executable input       "
            f"{'required' if gates.require_tier1 else 'not required'}"
            "   gate exists, off by default",
            title="constraints and flags - these never withhold",
            expand=False,
        )
    )


@app.command("rebuild")
def rebuild_cmd(
    db: Path | None = typer.Option(None),
    root: Path | None = typer.Option(None, help="Archive root"),
    recent: int = typer.Option(
        10, help="Replay only the last N snapshots; 0 replays the whole archive"
    ),
) -> None:
    """Reconstruct the database from archived snapshots and the tape.

    The database is derived state and is never committed; this is how history
    is restored from the durable files.
    """
    store = _store(db)
    target = root or ARCHIVE_ROOT
    with console.status("replaying archive..."):
        count = rebuild(store, target, recent=recent or None)
    stats = coverage(target)
    console.print(
        Panel(
            f"replayed   {count} snapshots\n"
            f"tape rows  {stats['tape_rows']} across {stats['index_dates']} index dates",
            title="rebuild",
            expand=False,
        )
    )
    store.close()


@app.command("verify")
def verify_cmd(
    root: Path | None = typer.Option(None, help="Archive root"),
) -> None:
    """Recompute every published value from archived inputs and compare.

    Exits non-zero on any mismatch, so CI fails when the series stops being
    reproducible from its own archive.
    """
    target = root or ARCHIVE_ROOT
    with console.status("recomputing from archive..."):
        report = verify(target)

    console.print(
        Panel(
            f"checked      {report.checked}\n"
            f"reproduced   {report.matched}\n"
            f"mismatched   {len(report.mismatches)}\n"
            f"unverifiable {len(report.unverifiable)}\n"
            f"dangling     {len(report.dangling)}",
            title="verify",
            expand=False,
        )
    )

    for note in report.dangling:
        console.print(f"  [bold red]dangling[/] {note}")
    for note in report.methodology_drift:
        console.print(f"  [yellow]methodology[/] {note}")
    for item in report.unverifiable:
        console.print(f"  [dim]unverifiable[/] {item.index_code} {item.index_date}: {item.detail}")
    for item in report.mismatches:
        console.print(
            f"  [bold red]mismatch[/] {item.index_code} {item.index_date}: "
            f"published {_fmt(item.published)}, recomputed {_fmt(item.recomputed)} "
            f"({item.detail})"
        )

    if not report.ok:
        raise typer.Exit(1)
    console.print("[green]series reproduces from its archive[/]")


@app.command("calibrate")
def calibrate_cmd() -> None:
    """Measure the commitment factors against what venues actually charge.

    Where a venue sells the same hardware under two commitment types, the
    ratio is a direct observation of what it charges for the difference --
    unless that ratio is identical on every SKU, in which case it is a
    discount policy and carries no information.
    """
    from .providers import collect_all

    with console.status("collecting from venues..."):
        collection = collect_all()

    evidence = observed_ratios(collection.observations)
    rows = compare_to_schedule(evidence)
    if not rows:
        console.print("[yellow]no venue priced the same hardware two ways[/]")
        raise typer.Exit(1)

    table = Table(title="[bold]commitment factors: observed vs asserted[/]", title_justify="left",
                  box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("venue", "tier", "n", "obs", "asserted", "err", "CV", "verdict"):
        table.add_column(
            column,
            justify="left" if column in ("venue", "tier", "verdict") else "right",
            no_wrap=True,
        )

    for row in sorted(rows, key=lambda r: (r["commitment"], r["source"])):
        if row["administered"]:
            verdict = "[yellow]administered[/]"
        elif row["error"] is not None and abs(row["error"]) <= 0.10:
            verdict = "[green]supports[/]"
        else:
            verdict = "[red]disagrees[/]"
        table.add_row(
            row["source"],
            row["commitment"],
            str(row["pairs"]),
            f"{row['observed']:.3f}",
            f"{row['asserted']:.2f}" if row["asserted"] else "--",
            f"{row['error']:+.1%}" if row["error"] is not None else "--",
            f"{row['cv']:.4f}",
            verdict,
        )
    console.print(table)
    console.print(
        "\nA constant ratio across every SKU is a pricing policy, not a market "
        "spread. Only a dispersed ratio is evidence."
    )


@app.command("forward")
def forward_cmd(
    spot: float = typer.Option(3.05, help="Current spot level, USD per GPU-hour"),
) -> None:
    """Invert committed-use discounts for an implied expected price decline.

    A GPU-hour is not storable, so there is no cost-of-carry relation and no
    forward can be bootstrapped from spot. What can be observed is the
    committed-use discount, which bundles expected decline together with the
    price of lock-in. The two are not separately identified, so the output
    here is a sensitivity range, not a point estimate.
    """
    points = load_committed_use()
    if not points:
        console.print("[yellow]no committed-use data in data/committed_use.json[/]")
        raise typer.Exit(1)

    console.print(
        Panel(
            "A GPU-hour cannot be stored, so F(T) = E[S(T)] + risk premium, with no\n"
            "arbitrage relation to pin it down. Compute prices like electricity, not\n"
            "like gold. Every number below is an expectation conditional on an\n"
            "assumption that nothing in public data identifies.",
            title="why there is no forward curve here",
            expand=False,
        )
    )

    table = Table(title="[bold]implied annual decline by assumed risk premium[/]", title_justify="left",
                  box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("vendor")
    table.add_column("tenor", justify="right")
    table.add_column("of on-demand", justify="right")
    for premium in (0.0, 0.05, 0.10, 0.15, 0.20):
        table.add_column(f"pi={premium:.0%}", justify="right")

    for point in sorted(points, key=lambda p: (p.vendor, p.tenor_years)):
        cells = [
            f"{row['annual_decline']:.1%}" for row in premium_sensitivity(point)
        ]
        table.add_row(
            point.vendor,
            f"{point.tenor_years:.0f}y",
            f"{point.price_ratio:.0%}",
            *cells,
        )
    console.print(table)
    console.print(
        "[dim]pi is the share of the discount attributed to lock-in and price "
        "certainty rather than to expected decline.[/]"
    )

    console.print()
    projection = Table(title=f"[bold]expected level from spot ${spot:.2f}/GPU-hr[/]", title_justify="left",
                     box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("assumption", "1 year", "2 years", "3 years"):
        projection.add_column(column, justify="left" if column == "assumption" else "right")
    for premium in (0.0, 0.10, 0.20):
        rate = implied_decline(0.60, 1.0, premium)
        projection.add_row(
            f"AWS 1y discount, pi={premium:.0%}  ({annual_decline_pct(rate):.1%}/yr)",
            *[f"${implied_forward_level(spot, rate, h):.2f}" for h in (1, 2, 3)],
        )
    console.print(projection)

    rows = consistency_check(points)
    if rows:
        console.print()
        console.print("[bold]term-structure consistency[/]")
        for row in rows:
            tenors = ", ".join(f"{t:.0f}y" for t in row["tenors"])
            implied = ", ".join(f"{annual_decline_pct(r):.1%}" for r in row["implied"])
            verdict = "[green]consistent[/]" if row["consistent"] else "[yellow]inconsistent[/]"
            console.print(
                f"  {row['vendor']:8} {tenors} imply {implied} -> {verdict} "
                f"(spread {row['spread']:.3f})"
            )
        console.print(
            "[dim]Under a constant decline rate one tenor determines the other. A wide\n"
            "spread means lock-in cost grows with term, which it plainly does, so the\n"
            "constant-rate model is the floor of a more honest term-dependent one.[/]"
        )


@app.command("sensitivity")
def sensitivity_cmd(
    live: bool = typer.Option(
        False, help="Collect fresh instead of reading the newest archived snapshot"
    ),
) -> None:
    """Measure how much of each fixing rests on the adjustment schedule.

    Section 4 of the methodology concedes its factors are judgement and bounds
    them. This answers the question that concession leaves open: how far does
    the fixing actually move because of them?
    """
    from .archive import list_snapshots, read_snapshot
    from .normalize import prepare_quotes
    from .providers import collect_all

    if live:
        with console.status("collecting from venues..."):
            observations = collect_all().observations
        provenance = "live collection"
    else:
        snapshots = list_snapshots(ARCHIVE_ROOT)
        if not snapshots:
            console.print("[yellow]no archived snapshot; pass --live[/]")
            raise typer.Exit(1)
        observations = read_snapshot(snapshots[-1]).observations
        provenance = snapshots[-1].name

    quotes, _ = prepare_quotes(observations)
    rows = exposure_all(quotes, DEFAULT_GATES)

    console.print(
        Panel(
            "The counterfactual recomputes each fixing from inputs that conformed to\n"
            "the benchmark contract as observed, discarding every adjusted one. It is\n"
            "not a better estimate — it throws away most of the sample and leans on\n"
            "whichever venues happen to sell the benchmark configuration. It measures\n"
            "dependence on judgement, and nothing else.",
            title=f"adjustment exposure ({provenance})",
            expand=False,
        )
    )

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("index", "published", "conforming only", "shift", "conforming", "adj weight", "still publishable"):
        table.add_column(column, justify="left" if column == "index" else "right", no_wrap=True)

    for row in rows:
        table.add_row(
            row.index_code,
            _fmt(row.published),
            _fmt(row.conforming_only),
            f"{row.shift:+.1%}" if row.shift is not None else "--",
            f"{row.conforming_quotes}/{row.total_quotes}",
            f"{row.weight_share_adjusted:.0%}",
            "[green]yes[/]" if row.publishable_without_adjustment else "[yellow]no[/]",
        )
    console.print(table)

    factors: dict[str, float] = {}
    for row in rows:
        for name, share in row.by_factor.items():
            factors[name] = max(factors.get(name, 0.0), share)
    if factors:
        console.print("\n[bold]share of inputs touched by each factor, highest across indices[/]")
        for name, share in sorted(factors.items(), key=lambda kv: -kv[1]):
            console.print(f"  {name:14} {share:.0%}")


@app.command("dispersion")
def dispersion_cmd(
    index_code: str = typer.Argument(...),
    index_date: str | None = typer.Argument(None, help="Defaults to the latest fixing"),
) -> None:
    """Show the dispersion arithmetic for one fixing, step by step.

    The dispersion gate is the one that most often decides whether a value
    prints, and it is the number people ask to see worked. Reconstructing it by
    hand invites arithmetic mistakes in front of the person asking, so it is a
    command.
    """
    import statistics

    from .archive import read_tape
    from .estimator import MAD_TO_SIGMA

    if index_date is None:
        dates = [r["index_date"] for r in read_tape(ARCHIVE_ROOT)]
        if not dates:
            console.print("[yellow]no fixings on the tape[/]")
            raise typer.Exit(1)
        index_date = max(dates)

    est, detail = estimate_from_archive(ARCHIVE_ROOT, index_code, index_date)
    if est is None:
        console.print(f"[yellow]{detail}[/]")
        raise typer.Exit(1)

    prices = sorted(p.price for p in est.contributing)
    if len(prices) < 3:
        console.print(
            f"[yellow]{len(prices)} contributing providers — dispersion is not "
            "computable below three, and is reported as undefined rather than guessed[/]"
        )
        raise typer.Exit(1)

    median = statistics.median(prices)
    deviations = sorted(abs(p - median) for p in prices)
    mad = statistics.median(deviations)
    sigma = mad * MAD_TO_SIGMA
    dispersion = sigma / median
    ceiling = DEFAULT_GATES.max_dispersion
    passed = dispersion <= ceiling

    console.print(
        Panel(
            f"[bold]{index_code}[/]  ·  {index_date}\n"
            f"[dim]{len(prices)} contributing providers, after screening[/]",
            border_style="cyan" if passed else "yellow",
            expand=False,
        )
    )

    console.print("\n[bold]provider medians, sorted[/]")
    console.print("  " + "  ".join(f"{p:.3f}" for p in prices))

    console.print("\n[bold]deviations from the median, sorted[/]")
    console.print("  " + "  ".join(f"{d:.3f}" for d in deviations))

    steps = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, show_header=False)
    steps.add_column("step", no_wrap=True)
    steps.add_column("value", justify="right", no_wrap=True)
    steps.add_column("note", style="dim")
    steps.add_row("median of prices", f"{median:.4f}", "the centre, robustly")
    steps.add_row("MAD = median of deviations", f"{mad:.4f}", "typical distance from the centre")
    steps.add_row(f"x {MAD_TO_SIGMA}", f"{sigma:.4f}", "consistency constant, 1 / inverse-normal(0.75)")
    steps.add_row("/ median", f"[bold]{dispersion:.4f}[/]", "scale-free, so one ceiling fits every index")
    console.print()
    console.print(steps)

    verdict = (
        f"[bold green]PASS[/]  {dispersion:.3f} is within the {ceiling} ceiling"
        if passed
        else f"[bold red]FAIL[/]  {dispersion:.3f} exceeds the {ceiling} ceiling"
    )
    console.print(f"  {verdict}")
    if not passed:
        console.print(
            f"  [dim]prices span {prices[0]:.3f} to {prices[-1]:.3f}, a "
            f"{prices[-1] / prices[0]:.1f}x range — this is not one market, and a "
            "central estimate would misrepresent both ends[/]"
        )
    console.print()


@app.command("explain")
def explain_cmd(
    index_code: str = typer.Argument(...),
    index_date: str = typer.Argument(...),
) -> None:
    """Show how one value was derived, from the raw file to the fixing.

    ``audit`` shows the providers behind a number. This shows everything
    around them: what was collected, what was discarded and why, what survived
    the screen, and which gates the remaining sample had to clear. It is the
    answer to "walk me through how this value was produced".
    """
    from collections import Counter

    from .archive import SNAPSHOT_DIR, read_snapshot, read_tape
    from .calibrate import drop_administered
    from .estimator import estimate as run_estimate
    from .normalize import normalize_all

    rows = [
        r
        for r in read_tape(ARCHIVE_ROOT)
        if r["index_code"] == index_code and r["index_date"] == index_date
    ]
    if not rows:
        console.print(f"[yellow]no tape row for {index_code} on {index_date}[/]")
        raise typer.Exit(1)
    row = max(rows, key=lambda r: int(r["revision"]))

    snapshot = (row.get("snapshot") or "").strip()
    path = ARCHIVE_ROOT / SNAPSHOT_DIR / snapshot
    if not snapshot or not path.exists():
        console.print(f"[yellow]inputs unavailable: {snapshot or 'no snapshot named'}[/]")
        raise typer.Exit(1)

    observations = read_snapshot(path).observations
    informative, admin_flags = drop_administered(observations)
    quotes, reject_flags = normalize_all(informative)
    mine = [q for q in quotes if q.index_code == index_code]
    est = run_estimate(index_code, mine, DEFAULT_GATES)

    published = row["status"] == "published"
    headline = _fmt(float(row["value"])) if row["value"] else "withheld"
    exposure_row = exposure(index_code, mine, DEFAULT_GATES)

    def stage(number: int, title: str, rationale: str) -> None:
        """A numbered stage with the decision it embodies, not just its output."""
        console.print()
        console.rule(f"[bold cyan]{number}[/]  [bold]{title}[/]", align="left", style="cyan")
        console.print(f"[italic dim]{rationale}[/]")

    console.print(
        Panel(
            f"[bold]{headline}[/]     revision {row['revision']}"
            f"     methodology {row['methodology_version']}"
            f"\n[dim]derived from {snapshot}[/]",
            title=f"[bold]{index_code}[/]  ·  {index_date}",
            border_style="cyan" if published else "yellow",
            expand=False,
        )
    )

    # -- 1. collection ------------------------------------------------------
    rejected: Counter[str] = Counter()
    for flag in reject_flags:
        rejected[flag.code.replace("rejected_", "")] = int(flag.detail.split()[0])

    reasons = {
        "unmatched_model": "no benchmark contract for that GPU string; matching is exact, never fuzzy",
        "region_mismatch": "region disclosed and outside the US; region is screened, never adjusted",
        "nonpositive_price": "not a price",
        "over_adjusted": "past 1.75x the number describes the adjustment schedule, not the market",
    }

    stage(1, "Collection", "What the venues published, and what was discarded before it could count.")
    funnel = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    funnel.add_column("step", no_wrap=True)
    funnel.add_column("count", justify="right", no_wrap=True)
    funnel.add_column("decision")
    funnel.add_row("raw observations", f"[bold]{len(observations)}[/]", "[dim]exactly as each venue stated them[/]")
    dropped_admin = len(observations) - len(informative)
    if dropped_admin:
        funnel.add_row(
            "administered", f"[red]-{dropped_admin}[/]",
            "; ".join(f.detail for f in admin_flags),
        )
    for code, count in sorted(rejected.items(), key=lambda kv: -kv[1]):
        funnel.add_row(code.replace("_", " "), f"[red]-{count}[/]", reasons.get(code, ""))
    funnel.add_row("normalised", f"[bold green]{len(quotes)}[/]", "[dim]across all five indices[/]")
    funnel.add_row(f"for {index_code}", f"[bold green]{len(mine)}[/]", "[dim]this index only[/]")
    console.print(funnel)

    # -- 2. restatement -----------------------------------------------------
    stage(
        2, "Restating to the benchmark good",
        "An H100-hour is not one good. Every input is expressed as the standard "
        "contract or discarded -- and this is the most criticisable step, so it is measured.",
    )
    adj = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    adj.add_column("factor", no_wrap=True)
    adj.add_column("share of inputs touched", justify="right", no_wrap=True)
    if exposure_row.by_factor:
        for name, share in sorted(exposure_row.by_factor.items(), key=lambda kv: -kv[1]):
            adj.add_row(name.replace("_", " "), f"{share:.0%}")
    else:
        adj.add_row("[dim]none[/]", "[dim]every input conformed as observed[/]")
    console.print(adj)
    console.print(
        f"  [bold]{exposure_row.conforming_quotes}[/] of [bold]{exposure_row.total_quotes}[/] "
        f"inputs conformed natively; "
        f"[bold]{exposure_row.weight_share_adjusted:.0%}[/] of contributing weight rests on adjusted ones"
    )
    if exposure_row.conforming_only is not None and exposure_row.shift is not None:
        console.print(
            f"  recomputed from conforming inputs alone: {_fmt(exposure_row.conforming_only)} "
            f"([bold]{exposure_row.shift:+.1%}[/]) -- the schedule is influential, not decisive"
        )
    else:
        console.print(
            "  [yellow]no counterfactual[/] -- too few natively conforming inputs to clear the "
            "gates, so this index exists because of the adjustment schedule"
        )

    # -- 3. estimation ------------------------------------------------------
    contributing = est.contributing
    screened = [p for p in est.providers if p.screened_out]
    total_weight = sum(p.weight for p in contributing) or 1.0

    stage(
        3, "One vote per provider, then screen and weight",
        "Collapse to a median per venue so forty SKUs is one vote. Screen on MAD, which has a "
        "50% breakdown point. Weight by what an input proves, and cap any venue at 35%.",
    )
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("provider", no_wrap=True)
    table.add_column("median", justify="right", no_wrap=True)
    table.add_column("quotes", justify="right", no_wrap=True)
    table.add_column("tier", justify="center", no_wrap=True)
    table.add_column("weight", justify="right", no_wrap=True)
    table.add_column("share", justify="right", no_wrap=True)
    table.add_column("note")
    tier_label = {1: "[green]1[/]", 2: "2", 3: "[dim]3[/]"}
    for agg in sorted(contributing, key=lambda a: a.price):
        table.add_row(
            agg.provider, _fmt(agg.price), str(agg.quote_count),
            tier_label.get(int(agg.best_tier), "?"),
            f"{agg.weight:.2f}", f"{agg.weight / total_weight:.1%}", "",
        )
    for agg in sorted(screened, key=lambda a: a.price):
        table.add_row(
            f"[strike dim]{agg.provider}[/]", f"[dim]{_fmt(agg.price)}[/]",
            f"[dim]{agg.quote_count}[/]", f"[dim]{int(agg.best_tier)}[/]",
            "[dim]--[/]", "[dim]--[/]", f"[red]{agg.screen_reason}[/]",
        )
    console.print(table)
    console.print(
        f"  weighted mean of [bold]{len(contributing)}[/] surviving providers = "
        f"[bold cyan]{_fmt(est.value)}[/]"
        + (f"   [dim]({len(screened)} screened)[/]" if screened else "")
    )

    # -- 4. gates -----------------------------------------------------------
    stage(
        4, "Publication gates",
        "Every enabled gate must hold. A gap in the series is a fact about the market; "
        "an interpolated value is a fiction about it.",
    )
    gates = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    gates.add_column(" ", no_wrap=True)
    gates.add_column("gate", no_wrap=True)
    gates.add_column("detail")
    for gate in est.gates:
        gates.add_row(
            "[bold green]PASS[/]" if gate.passed else "[bold red]FAIL[/]",
            gate.name.replace("_", " "),
            gate.detail if gate.passed else f"[bold red]{gate.detail}[/]",
        )
    console.print(gates)

    console.print()
    if published:
        console.print(
            Panel(f"every gate cleared  ->  published at [bold]{headline}[/]",
                  border_style="green", expand=False)
        )
    else:
        console.print(
            Panel(
                f"the estimator produced [bold]{_fmt(est.value)}[/] and it was "
                f"[bold yellow]not published[/]\n"
                f"[dim]{row['withheld_reason'] or ''}[/]",
                border_style="yellow", expand=False,
            )
        )

    if est.flags:
        console.print("[dim]flags raised on this index[/]")
        for flag in est.flags:
            style = SEVERITY_STYLE.get(flag.severity, "")
            console.print(f"  [{style}]{flag.code:28}[/] {flag.detail}")


@app.command("export-web")
def export_web_cmd(
    out: Path = typer.Option(
        Path("web/data"), help="Directory to write the JSON bundle into"
    ),
) -> None:
    """Export the archive as JSON for the static demo site.

    Reads the snapshots and the tape only -- no database, no network -- and
    recomputes the newest fixing through the same path ``verify`` uses, so the
    site cannot disagree with the published record.
    """
    from .web import write_bundle

    written = write_bundle(ARCHIVE_ROOT, out)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("file")
    table.add_column("bytes", justify="right")
    for path in written:
        table.add_row(str(path.relative_to(Path.cwd()) if path.is_absolute() else path),
                      f"{path.stat().st_size:,}")
    console.print(table)


if __name__ == "__main__":
    app()
