"""The durable record: immutable raw inputs plus an append-only tape.

The SQLite store is fast to query but is *derived state* — it is rebuilt on
demand and never committed. Two things are durable instead:

``snapshots/<run>.jsonl.gz``
    Every raw observation exactly as the venue stated it, one file per
    collection run, never modified after it is written. This is what makes a
    published value reconstructible years later.

``series/index_values.csv``
    The publication record: one row per revision, append-only. It cannot be
    derived from the snapshots, because it records *what was published and
    when* — including values that were later superseded. Re-deriving it from
    inputs would quietly erase the revision history, which is the one thing a
    settlement dispute needs.

    Each row also names the snapshot it was computed from. That provenance
    link is what makes a value independently checkable: without it, a day
    carrying two runs cannot be verified at all, because there is no way to
    tell which inputs produced which print.

Together they mean the database can be deleted at any time and rebuilt, while
nothing about the published history is lost.
"""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .models import QualityFlag, RawObservation

SNAPSHOT_DIR = "snapshots"
SERIES_DIR = "series"
TAPE_NAME = "index_values.csv"

TAPE_COLUMNS = [
    "index_code",
    "index_date",
    "revision",
    "status",
    "value",
    "provider_count",
    "observation_count",
    "dispersion",
    "withheld_reason",
    "methodology_version",
    "published_at",
    "superseded_at",
    "revision_reason",
    "snapshot",
    # From 1.2.0: every marketplace venue whose vote the population floor
    # dropped from this index, with the priced counts that failed it, joined
    # by " | ". Blank when nothing was held out, and blank on every row
    # written before the column existed -- those rows never recorded it.
    "venue_holdouts",
]

#: Separator between hold-outs in ``venue_holdouts``. Not a semicolon, which
#: the reasons themselves use.
HOLDOUT_SEPARATOR = " | "


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H%M%SZ")


@dataclass
class ArchivedRun:
    path: Path
    captured_at: datetime
    observations: list[RawObservation]


# -- snapshots -------------------------------------------------------------


def write_snapshot(
    root: Path, observations: list[RawObservation], captured_at: datetime | None = None
) -> Path:
    """Write one collection run to an immutable gzipped JSONL file."""
    captured_at = captured_at or datetime.now(UTC)
    directory = root / SNAPSHOT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_stamp(captured_at)}.jsonl.gz"

    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for obs in observations:
            handle.write(obs.model_dump_json() + "\n")
    return path


def read_snapshot(path: Path) -> ArchivedRun:
    observations: list[RawObservation] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                observations.append(RawObservation.model_validate_json(line))

    captured = min((o.observed_at for o in observations), default=None)
    if captured is None:
        captured = datetime.strptime(path.name.split(".")[0], "%Y-%m-%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
    return ArchivedRun(path=path, captured_at=captured, observations=observations)


def list_snapshots(root: Path) -> list[Path]:
    directory = root / SNAPSHOT_DIR
    if not directory.exists():
        return []
    return sorted(directory.glob("*.jsonl.gz"))


# -- the tape --------------------------------------------------------------


def venue_holdouts(flags: list[QualityFlag], index_code: str) -> str:
    """The tape's ``venue_holdouts`` cell for one index: each floor hold-out, joined."""
    return HOLDOUT_SEPARATOR.join(
        f.detail
        for f in flags
        if f.code == "book_population_floor" and f.index_code == index_code
    )


def tape_path(root: Path) -> Path:
    return root / SERIES_DIR / TAPE_NAME


def append_to_tape(root: Path, rows: list[dict]) -> Path:
    """Append publication records. Existing rows are never rewritten."""
    path = tape_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    if exists:
        _widen_header(path)

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAPE_COLUMNS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _widen_header(path: Path) -> None:
    """Add columns appended to ``TAPE_COLUMNS`` since this tape was written.

    Columns are only ever appended, so an older tape's header is a prefix of
    today's. Appending a wider row under a narrower header would put the new
    field under no name at all, and the next ``stamp_superseded`` would drop
    it. So the header is widened first and older rows get the new fields
    blank: no value, date or reason is touched, which is the same class of
    rewrite ``stamp_superseded`` already makes.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None or header == TAPE_COLUMNS:
            return
        if header != TAPE_COLUMNS[: len(header)]:
            raise ValueError(
                f"{path} has columns {header}, which are not a prefix of {TAPE_COLUMNS}; "
                "refusing to append to a tape this build does not understand"
            )
        rows = list(reader)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TAPE_COLUMNS)
        pad = [""] * (len(TAPE_COLUMNS) - len(header))
        for row in rows:
            writer.writerow(row + pad)


def read_tape(root: Path) -> list[dict]:
    path = tape_path(root)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def stamp_superseded(root: Path) -> None:
    """Recompute ``superseded_at`` across the tape.

    A revision is superseded by the next revision of the same index and date.
    This is the one rewrite the tape permits, and it only ever fills a field
    that was previously blank -- values, dates, and reasons are untouched.
    """
    rows = read_tape(root)
    if not rows:
        return

    latest: dict[tuple[str, str], int] = {}
    published: dict[tuple[str, str, int], str] = {}
    for row in rows:
        key = (row["index_code"], row["index_date"])
        revision = int(row["revision"])
        latest[key] = max(latest.get(key, -1), revision)
        published[(*key, revision)] = row["published_at"]

    for row in rows:
        key = (row["index_code"], row["index_date"])
        revision = int(row["revision"])
        if revision < latest[key]:
            row["superseded_at"] = published[(*key, revision + 1)]

    path = tape_path(root)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAPE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def live_tape_values(root: Path) -> dict[tuple[str, str], dict]:
    """The current view: the highest revision for each index and date."""
    live: dict[tuple[str, str], dict] = {}
    for row in read_tape(root):
        key = (row["index_code"], row["index_date"])
        if key not in live or int(row["revision"]) > int(live[key]["revision"]):
            live[key] = row
    return live


def parse_index_date(row: dict) -> date:
    return date.fromisoformat(row["index_date"])
