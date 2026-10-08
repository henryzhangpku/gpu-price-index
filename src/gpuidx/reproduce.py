"""Rebuild and verify the series from the archive.

Two operations, and the distinction between them is the point.

``rebuild``
    Reconstruct the working database from archived snapshots and the tape.
    This is how the Action gets its history back without committing a binary
    database: the state a value depended on -- prior runs, prior fixings -- is
    restored from durable files.

``verify``
    Recompute every published value from its archived raw inputs and compare
    against what the tape says was published. This is the property that makes
    the benchmark auditable rather than merely logged: given the archive and
    the methodology version, anyone can derive the same numbers, or find out
    exactly where they cannot.

A verify failure is not necessarily a bug. It is the correct alarm when the
methodology changed without a version bump, when a snapshot was altered, or
when a value was published from inputs that were never archived. All three
are things a benchmark administrator has to be able to detect.

Every tape row is recomputed under the methodology version it names, looked
up in ``spec.METHODOLOGIES``, not under today's defaults. A change of
methodology therefore leaves the historical series exactly as verifiable as
it was; the only version-related failure left is a row naming a version this
build does not carry, which is a real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .archive import (
    SNAPSHOT_DIR,
    list_snapshots,
    live_tape_values,
    read_snapshot,
    read_tape,
    venue_holdouts,
)
from .estimator import Estimate, estimate
from .normalize import prepare_quotes
from .spec import CONTRACTS, CURRENT_METHODOLOGY, Gates, Methodology, methodology_for
from .store import Store, _iso_z

#: Values are compared to the cent. Tighter than this and floating-point
#: association order across a rebuild would produce spurious failures.
TOLERANCE = 0.005


@dataclass
class Mismatch:
    index_code: str
    index_date: str
    published: float | None
    recomputed: float | None
    detail: str


@dataclass
class VerifyReport:
    checked: int = 0
    matched: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    unverifiable: list[Mismatch] = field(default_factory=list)
    #: Rows naming a methodology version this build cannot reproduce. Not a
    #: version *difference* -- those are expected and handled -- but a version
    #: with no registered behaviour, which makes the row unverifiable.
    methodology_drift: list[str] = field(default_factory=list)
    #: Tape rows naming a snapshot that is no longer on disk. Superseded
    #: revisions are not recomputed, so without this check their inputs could
    #: quietly disappear and nothing would notice.
    dangling: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.methodology_drift and not self.dangling


def rebuild(
    store: Store,
    root: Path,
    gates: Gates | Methodology | None = None,
    recent: int | None = None,
) -> int:
    """Replay archived snapshots into a store, restoring run history.

    Index values are not recomputed here -- they are read from the tape, which
    is the authoritative publication record. Recomputing them would renumber
    revisions and silently discard the history of what was published when.

    ``recent`` bounds how many snapshots are replayed. The quality checks only
    look back a handful of runs, so a daily job does not need to re-ingest a
    year of archive to have the history it depends on. The full tape is always
    restored regardless -- it is small, and level-shift detection needs the
    entire published series, not a window of it. Pass ``None`` to replay
    everything, which is what an audit wants.
    """
    methodology = _methodology_of(gates)
    snapshots = list_snapshots(root)
    if recent is not None and recent > 0:
        snapshots = snapshots[-recent:]

    for path in snapshots:
        archived = read_snapshot(path)
        per_provider: dict[str, int] = {}
        for obs in archived.observations:
            per_provider[obs.source] = per_provider.get(obs.source, 0) + 1

        run_id = store.start_run(per_provider)
        store.record_observations(run_id, archived.observations)
        quotes, _ = prepare_quotes(archived.observations, methodology)
        store.record_quotes(run_id, quotes)

    _restore_tape(store, root)
    return len(snapshots)


def _restore_tape(store: Store, root: Path) -> None:
    """Load the publication record verbatim, revision numbering intact."""
    rows = read_tape(root)
    if not rows:
        return

    with store.tx() as conn:
        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO index_values (index_code, index_date, revision,"
                " status, value, provider_count, observation_count, dispersion,"
                " withheld_reason, methodology_version, published_at, superseded_at,"
                " revision_reason, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    row["index_code"],
                    row["index_date"],
                    int(row["revision"]),
                    row["status"],
                    float(row["value"]) if row["value"] else None,
                    int(row["provider_count"]),
                    int(row["observation_count"]),
                    float(row["dispersion"]) if row["dispersion"] else None,
                    row["withheld_reason"] or None,
                    row["methodology_version"],
                    _iso_z(row["published_at"]),
                    _iso_z(row["superseded_at"]) if row["superseded_at"] else None,
                    row["revision_reason"] or None,
                ),
            )


def verify(root: Path, gates: Gates | None = None) -> VerifyReport:
    """Recompute published values from archived inputs and compare to the tape."""
    # Passing gates to verify is a diagnostic ("would this have published
    # under stricter gates?"); it overrides the gates of every row's own
    # methodology and is expected to produce mismatches.
    gates_override = gates
    report = VerifyReport()

    # Index snapshots by filename. A value is checked against the exact run
    # that produced it, never against a pooled day: two runs on one date
    # produce different inputs, and pooling them would compare a published
    # value against a set of observations that never existed together.
    available = {path.name: path for path in list_snapshots(root)}
    cache: dict[str, list] = {}

    # Integrity sweep across *every* revision, not just the live ones. A
    # superseded value is still a published value: someone may have settled
    # against it, and its inputs have to remain on file.
    for row in read_tape(root):
        name = (row.get("snapshot") or "").strip()
        if name and name not in available:
            report.dangling.append(
                f"{row['index_code']} {row['index_date']} rev {row['revision']} "
                f"names missing snapshot {name}"
            )

    for (index_code, index_date), row in sorted(live_tape_values(root).items()):
        report.checked += 1

        methodology = methodology_for(row["methodology_version"])
        if methodology is None:
            report.methodology_drift.append(
                f"{index_code} {index_date} published under methodology "
                f"{row['methodology_version']}, which this build does not carry "
                f"(known: {', '.join(sorted(_known_versions()))})"
            )
            continue
        if gates_override is not None:
            methodology = methodology.model_copy(update={"gates": gates_override})

        name = (row.get("snapshot") or "").strip()
        if not name:
            report.unverifiable.append(
                Mismatch(
                    index_code,
                    index_date,
                    _as_float(row["value"]),
                    None,
                    "tape row predates snapshot provenance and names no inputs",
                )
            )
            continue
        if name not in available:
            report.unverifiable.append(
                Mismatch(
                    index_code,
                    index_date,
                    _as_float(row["value"]),
                    None,
                    f"named snapshot {name} is missing from the archive",
                )
            )
            continue

        if name not in cache:
            cache[name] = read_snapshot(available[name]).observations
        observations = cache[name]

        quotes, prep_flags = prepare_quotes(observations, methodology)
        relevant = [q for q in quotes if q.index_code == index_code]
        recomputed = estimate(index_code, relevant, methodology)

        published_value = _as_float(row["value"])
        recomputed_value = recomputed.value if recomputed.passed else None

        # From 1.2.0 the tape records which marketplace votes the population
        # floor dropped. That record is checked like the value: a hold-out
        # the archive no longer supports is as much a mismatch as a price.
        if gates_override is None and methodology.gates.book_floor_basis == "priced":
            recorded = (row.get("venue_holdouts") or "").strip()
            derived = venue_holdouts(prep_flags, index_code)
            if recorded != derived:
                report.mismatches.append(
                    Mismatch(
                        index_code,
                        index_date,
                        published_value,
                        recomputed_value,
                        f"venue hold-outs differ: tape says {recorded or 'none'!r}, "
                        f"archive gives {derived or 'none'!r}",
                    )
                )
                continue

        if published_value is None and recomputed_value is None:
            report.matched += 1
            continue

        if published_value is None or recomputed_value is None:
            report.mismatches.append(
                Mismatch(
                    index_code,
                    index_date,
                    published_value,
                    recomputed_value,
                    "published and recomputed disagree on whether to publish at all",
                )
            )
            continue

        if abs(published_value - recomputed_value) <= TOLERANCE:
            report.matched += 1
        else:
            report.mismatches.append(
                Mismatch(
                    index_code,
                    index_date,
                    published_value,
                    recomputed_value,
                    f"differs by ${abs(published_value - recomputed_value):.4f}",
                )
            )

    return report


def estimate_from_archive(
    root: Path,
    index_code: str,
    index_date: str,
    revision: int | None = None,
    gates: Gates | None = None,
) -> tuple[Estimate | None, str]:
    """Recompute one fixing's provider breakdown from its own archived inputs.

    ``audit`` needs this because the contributions table is written by a live
    publish and a rebuilt store has none. Without it a fresh clone can restore
    every published value from the tape and still not explain a single one --
    which is the state every reader of this repository is in.

    Deriving the breakdown from the named snapshot is also the stronger answer.
    Reading the table tells you what was computed once; recomputing tells you
    what the archived inputs still support, and that is what an audit is for.

    Returns the estimate and the snapshot it came from, or None and the reason
    it could not be derived.
    """
    candidates = [
        row
        for row in read_tape(root)
        if row["index_code"] == index_code and row["index_date"] == index_date
    ]
    if not candidates:
        return None, f"no tape row for {index_code} on {index_date}"

    if revision is None:
        row = max(candidates, key=lambda r: int(r["revision"]))
    else:
        matching = [r for r in candidates if int(r["revision"]) == revision]
        if not matching:
            return None, f"no revision {revision} for {index_code} on {index_date}"
        row = matching[0]

    methodology = methodology_for(row["methodology_version"])
    if methodology is None:
        return None, (
            f"published under methodology {row['methodology_version']}, "
            f"which this build does not carry -- recomputing would explain it "
            "under rules it was not produced by"
        )

    name = (row.get("snapshot") or "").strip()
    if not name:
        return None, "tape row predates snapshot provenance and names no inputs"

    path = root / SNAPSHOT_DIR / name
    if not path.exists():
        return None, f"named snapshot {name} is missing from the archive"

    if gates is not None:
        methodology = methodology.model_copy(update={"gates": gates})
    quotes, _ = prepare_quotes(read_snapshot(path).observations, methodology)
    relevant = [q for q in quotes if q.index_code == index_code]
    return estimate(index_code, relevant, methodology), name


@dataclass
class ImpactRow:
    """One live fixing, as published and as another methodology would price it."""

    index_code: str
    index_date: str
    published_version: str
    #: The value the comparison is made against: the tape's own value, or a
    #: recomputation under ``against`` when one is given. None == withheld.
    baseline: float | None
    #: The same snapshot under the target methodology. None == withheld.
    recomputed: float | None

    @property
    def differs(self) -> bool:
        if self.baseline is None or self.recomputed is None:
            # Withheld on one side only is a difference; on both it is not.
            return (self.baseline is None) != (self.recomputed is None)
        return abs(self.recomputed - self.baseline) > TOLERANCE

    @property
    def change(self) -> float | None:
        """Relative change from baseline, when both sides published."""
        if self.baseline is None or self.recomputed is None or self.baseline == 0:
            return None
        return self.recomputed / self.baseline - 1.0


def version_impact(
    root: Path, target: Methodology, against: Methodology | None = None
) -> list[ImpactRow]:
    """What every live fixing would have been under ``target``.

    This is the back-test a methodology change publishes rather than applies.
    Each live tape row is recomputed from the exact snapshot it names under
    ``target`` and set beside the value the tape carries -- or, with
    ``against``, beside a recomputation of the same snapshot under that
    methodology, which isolates one change from everything the versions
    between them also changed. Nothing is written: a back-test is a document,
    not a revision.
    """
    available = {path.name: path for path in list_snapshots(root)}
    cache: dict[tuple[str, str], tuple[list, list]] = {}
    observations: dict[str, list] = {}

    def prepared(name: str, methodology: Methodology) -> list:
        key = (name, methodology.model_dump_json())
        if key not in cache:
            if name not in observations:
                observations[name] = read_snapshot(available[name]).observations
            cache[key] = prepare_quotes(observations[name], methodology)
        return cache[key][0]

    def value(name: str, index_code: str, methodology: Methodology) -> float | None:
        quotes = [q for q in prepared(name, methodology) if q.index_code == index_code]
        est = estimate(index_code, quotes, methodology)
        return est.value if est.passed else None

    out: list[ImpactRow] = []
    for (index_code, index_date), row in sorted(live_tape_values(root).items()):
        name = (row.get("snapshot") or "").strip()
        if not name or name not in available:
            continue
        baseline = (
            _as_float(row["value"]) if against is None else value(name, index_code, against)
        )
        out.append(
            ImpactRow(
                index_code=index_code,
                index_date=index_date,
                published_version=row["methodology_version"],
                baseline=baseline,
                recomputed=value(name, index_code, target),
            )
        )
    return out


def _as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def coverage(root: Path) -> dict[str, int]:
    """How much archived history exists, for the operator's benefit."""
    snapshots = list_snapshots(root)
    tape = read_tape(root)
    dates = {row["index_date"] for row in tape}
    return {
        "snapshots": len(snapshots),
        "tape_rows": len(tape),
        "index_dates": len(dates),
        "indices": len(CONTRACTS),
    }


def _methodology_of(gates: Gates | Methodology | None) -> Methodology:
    if gates is None:
        return CURRENT_METHODOLOGY
    if isinstance(gates, Methodology):
        return gates
    if gates == CURRENT_METHODOLOGY.gates:
        return CURRENT_METHODOLOGY
    return CURRENT_METHODOLOGY.model_copy(update={"gates": gates})


def _known_versions() -> list[str]:
    from .spec import METHODOLOGIES

    return list(METHODOLOGIES)
