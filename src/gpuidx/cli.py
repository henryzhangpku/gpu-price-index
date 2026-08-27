"""Operator interface.

Five verbs, matching the five things a benchmark administrator actually does:
run the cycle, look at today's tape, audit how a number was built, answer an
as-of question, and correct a bad print.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .calibrate import compare_to_schedule, observed_ratios
from .pipeline import run_daily
from .reproduce import coverage, rebuild, verify
from .spec import CONTRACTS, DEFAULT_GATES
from .store import Store

#: Repository root, where snapshots/ and series/ live.
ARCHIVE_ROOT = Path(__file__).resolve().parents[2]

app = typer.Typer(add_completion=False, help="GPU rental price benchmark reference implementation")
console = Console()

SEVERITY_STYLE = {"info": "dim", "warn": "yellow", "error": "bold red"}


def _store(db: Optional[Path]) -> Store:
    return Store(db)


def _fmt(value: float | None) -> str:
    return f"${value:.3f}" if value is not None else "--"


@app.command()
def publish(
    db: Optional[Path] = typer.Option(None, help="SQLite path"),
    index_date: Optional[str] = typer.Option(None, "--date", help="Index date (YYYY-MM-DD)"),
    reason: Optional[str] = typer.Option(None, help="Revision reason, if correcting"),
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
    db: Optional[Path] = typer.Option(None),
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
    db: Optional[Path] = typer.Option(None),
    revision: Optional[int] = typer.Option(None, help="Defaults to the live revision"),
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
    store.close()


@app.command("as-of")
def as_of(
    index_code: str = typer.Argument(...),
    index_date: str = typer.Argument(..., help="Index date (event time)"),
    knowledge_time: str = typer.Argument(..., help="ISO timestamp (knowledge time)"),
    db: Optional[Path] = typer.Option(None),
) -> None:
    """Answer: what did we say for this date, as known at that moment?"""
    store = _store(db)
    when = datetime.fromisoformat(knowledge_time)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

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
    db: Optional[Path] = typer.Option(None),
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
    """Print the benchmark contract definitions and active gates."""
    for contract in CONTRACTS.values():
        console.print(Panel(contract.describe(), title=f"{contract.index_code} - {contract.display_name}"))
    gates = DEFAULT_GATES
    console.print(
        Panel(
            f"min providers          {gates.min_providers}\n"
            f"min observations       {gates.min_observations}\n"
            f"max dispersion         {gates.max_dispersion}\n"
            f"max provider weight    {gates.max_provider_weight_share:.0%}\n"
            f"review move threshold  {gates.review_move_threshold:.0%}",
            title="publication gates",
            expand=False,
        )
    )


@app.command("rebuild")
def rebuild_cmd(
    db: Optional[Path] = typer.Option(None),
    root: Optional[Path] = typer.Option(None, help="Archive root"),
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
    root: Optional[Path] = typer.Option(None, help="Archive root"),
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
            f"unverifiable {len(report.unverifiable)}",
            title="verify",
            expand=False,
        )
    )

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


if __name__ == "__main__":
    app()
