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
from .sensitivity import exposure_all
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

    table = Table(title="daily fixing", header_style="bold")
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

    contract = CONTRACTS.get(index_code)
    if contract:
        console.print(Panel(contract.describe(), title=contract.display_name, expand=False))

    table = Table(header_style="bold")
    for column in ("date", "value", "rev", "prov", "obs", "disp", "status"):
        table.add_column(column, justify="right" if column != "status" else "left")
    for row in rows:
        table.add_row(
            row["index_date"],
            _fmt(row["value"]),
            str(row["revision"]),
            str(row["provider_count"]),
            str(row["observation_count"]),
            f"{row['dispersion']:.3f}" if row["dispersion"] is not None else "--",
            row["status"] if row["status"] == "published" else f"[red]{row['status']}[/]",
        )
    console.print(table)
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

    table = Table(title="contributions", header_style="bold")
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

    table = Table(header_style="bold")
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
    for contract in CONTRACTS.values():
        console.print(Panel(contract.describe(), title=f"{contract.index_code} - {contract.display_name}"))
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

    table = Table(title="commitment factors: observed vs asserted", header_style="bold")
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

    table = Table(title="implied annual decline by assumed risk premium", header_style="bold")
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
    projection = Table(title=f"expected level from spot ${spot:.2f}/GPU-hr", header_style="bold")
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

    table = Table(header_style="bold")
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

    table = Table(header_style="bold")
    table.add_column("file")
    table.add_column("bytes", justify="right")
    for path in written:
        table.add_row(str(path.relative_to(Path.cwd()) if path.is_absolute() else path),
                      f"{path.stat().st_size:,}")
    console.print(table)


if __name__ == "__main__":
    app()
