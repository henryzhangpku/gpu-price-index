"""Polite, cached access to the Internet Archive's Wayback Machine.

This is the only module in the backfill that opens a socket, in the same
spirit as ``providers/`` for the live index. Everything downstream reads the
on-disk cache, so the reconstructed series rebuilds offline and a value can
always be traced to the exact archived bytes it was parsed from.

Two things are cached, both under ``backfill/``:

``cdx/<source>.json``
    The capture listing the CDX API returned for a source's URLs. Snapshot
    *selection* is a function of this listing, so caching it makes selection
    reproducible too -- a re-run next year would otherwise see captures added
    since and pick different days.

``archive/<source>/<timestamp>.html.gz``
    The archived response body, fetched with the ``id_`` flag so the bytes
    are the page as captured rather than the Wayback toolbar's rewrite of it.
    A file is only ever written under the timestamp the archive actually
    served: see ``fetch_snapshot`` for the redirects that make this necessary.

The CDX API's server-side ``filter`` and ``collapse`` parameters are not used:
during development they returned HTTP 503 ("Temporarily Offline") reliably
while plain listings succeeded, so status filtering and monthly collapsing are
done client-side, where they are also easier to test.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

USER_AGENT = (
    "gpuidx-backfill/0.1 (research reconstruction of archived public rate cards; "
    "https://github.com/henryzhangpku/gpu-price-index)"
)
CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
TIMEMAP_ENDPOINT = "https://web.archive.org/web/timemap/json"
SNAPSHOT_TEMPLATE = "https://web.archive.org/web/{timestamp}id_/{original}"

#: Minimum seconds between any two requests. Well under the "few per second"
#: ceiling, because the archive is a shared public resource.
MIN_INTERVAL = 1.5
#: Backoff schedule for 429/5xx and transport errors, in seconds. Many short
#: retries rather than a few long ones: the CDX server's 503 arrives after a
#: fixed ~5 s backend timeout and a retry a few seconds later usually succeeds,
#: whereas a long wait buys nothing.
BACKOFF = (3, 5, 8, 12, 20, 30, 45, 60, 90, 120)

BACKFILL_DIR = "backfill"


@dataclass(frozen=True)
class Capture:
    """One row of a CDX listing."""

    timestamp: str  # YYYYMMDDhhmmss, UTC
    original: str
    statuscode: str
    digest: str
    mimetype: str

    @property
    def month(self) -> str:
        return f"{self.timestamp[:4]}-{self.timestamp[4:6]}"

    @property
    def day(self) -> str:
        return f"{self.timestamp[:4]}-{self.timestamp[4:6]}-{self.timestamp[6:8]}"

    @property
    def snapshot_url(self) -> str:
        return SNAPSHOT_TEMPLATE.format(timestamp=self.timestamp, original=self.original)


class Fetcher:
    """Rate-limited HTTP with retries. One instance per run."""

    def __init__(self, client: httpx.Client | None = None, min_interval: float = MIN_INTERVAL):
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(120.0, connect=20.0),
            follow_redirects=True,
        )
        self.min_interval = min_interval
        self._last = 0.0
        self.requests = 0

    def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        follow: bool = True,
        retries: tuple[int, ...] = BACKOFF,
    ) -> httpx.Response | None:
        """GET with politeness and backoff; None after the schedule is exhausted.

        With ``follow=False`` a redirect is returned to the caller rather than
        followed, so the caller can see where the archive is sending it.
        """
        for delay in (0, *retries):
            if delay:
                time.sleep(delay)
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                response = self.client.get(url, params=params, follow_redirects=follow)
            except httpx.HTTPError:
                continue
            if response.status_code == 200:
                return response
            if not follow and response.status_code in (301, 302, 303, 307, 308):
                return response
            if response.status_code == 404:
                return None
            # 429, 5xx, and the archive's HTML "Temporarily Offline" 503.
        return None


def cdx_path(root: Path, source: str) -> Path:
    return root / BACKFILL_DIR / "cdx" / f"{source}.json"


def parse_cdx(rows: list[list[str]]) -> list[Capture]:
    """Turn a JSON CDX response (header row first) into captures."""
    if not rows:
        return []
    header, *body = rows
    idx = {name: i for i, name in enumerate(header)}
    out = []
    for row in body:
        out.append(
            Capture(
                timestamp=row[idx["timestamp"]],
                original=row[idx["original"]],
                statuscode=row[idx["statuscode"]],
                digest=row[idx["digest"]],
                mimetype=row[idx["mimetype"]],
            )
        )
    return out


def _json_rows(response: httpx.Response | None) -> list[list[str]] | None:
    if response is None:
        return None
    text = response.text.strip()
    if not text:
        return []
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        return None
    return rows if isinstance(rows, list) else None


def list_captures(
    fetcher: Fetcher, url: str, years: tuple[int, ...] = (2023, 2024, 2025, 2026)
) -> list[list[str]] | None:
    """The capture listing for one URL over ``years``, header row first.

    Asks the timemap endpoint for the whole range in one request, which the
    archive answered far more reliably than the CDX endpoint during
    development; falls back to CDX a year at a time. Returns None if neither
    produced a complete listing -- a partial listing would silently thin the
    series, which is worse than a source that is visibly missing.
    """
    fields = "timestamp,original,mimetype,statuscode,digest"
    rows = _json_rows(
        fetcher.get(
            TIMEMAP_ENDPOINT,
            params={"url": url, "from": str(years[0]), "to": str(years[-1]), "fl": fields},
        )
    )
    if rows is not None:
        return rows

    merged: list[list[str]] = []
    for year in years:
        params = {"url": url, "output": "json", "from": str(year), "to": str(year), "fl": fields}
        rows = _json_rows(fetcher.get(CDX_ENDPOINT, params=params))
        if rows is None:
            return None
        if not rows:
            continue
        if not merged:
            merged.append(rows[0])
        merged.extend(rows[1:])
    return merged


def usable(captures: list[Capture]) -> list[Capture]:
    """Captures whose own record is an archived 200 HTML response.

    ``warc/revisit`` records ("same bytes as an earlier capture") are not
    used. Requesting one makes the archive redirect to some other capture --
    on one development check, a request for 18 April 2023 was served the page
    of 20 May -- so its timestamp is not the moment of the bytes it returns.
    Redirects, errors and non-HTML records are dropped for the same reason.
    """
    out = [c for c in captures if c.statuscode == "200" and c.mimetype.startswith("text/html")]
    return sorted(out, key=lambda c: c.timestamp)


def snapshot_path(root: Path, source: str, capture: Capture) -> Path:
    return root / BACKFILL_DIR / "archive" / source / f"{capture.timestamp}.html.gz"


#: A snapshot fetch gives up sooner than a listing: another capture in the
#: same period is usually a better use of the next request than a long wait.
SNAPSHOT_RETRIES = (3, 8, 20)

_WAYBACK_TS = re.compile(r"/web/(\d{14})id_/")


def served_timestamp(url: str) -> str | None:
    m = _WAYBACK_TS.search(url)
    return m.group(1) if m else None


def fetch_snapshot(fetcher: Fetcher, root: Path, source: str, capture: Capture) -> Path | None:
    """Fetch one archived page into the cache, or return the cached copy.

    The archive does not always serve the capture asked for: a request for one
    timestamp can be redirected to a neighbouring capture days or weeks away
    (seen on 2 of 8 spot checks during development). A page served from a
    different moment would be filed under the wrong date, so the fetch refuses
    any redirect that changes the timestamp and returns None, and the caller
    tries the next capture in the period. A redirect that keeps the timestamp
    (a scheme or host canonicalisation) is followed.
    """
    path = snapshot_path(root, source, capture)
    if path.exists():
        return path
    url = capture.snapshot_url
    for _hop in range(3):
        response = fetcher.get(url, follow=False, retries=SNAPSHOT_RETRIES)
        if response is None:
            return None
        if response.status_code == 200:
            break
        location = response.headers.get("location", "")
        target = httpx.URL(url).join(location)
        if served_timestamp(str(target)) != capture.timestamp:
            return None
        url = str(target)
    else:
        return None
    if not response.content:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 makes the gzip bytes a pure function of the content.
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(response.content)
    return path


def read_cached(path: Path) -> bytes:
    with gzip.open(path, "rb") as handle:
        return handle.read()


def sha256_of(path: Path) -> str:
    return hashlib.sha256(read_cached(path)).hexdigest()
