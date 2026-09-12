"""Stamp asset URLs with a content hash, or check that they are already stamped.

A hand-written version string is a cache-busting scheme that works until
somebody forgets, and the failure is silent and expensive: the page keeps
serving the previous script, everything looks broken in a way the source does
not explain, and the obvious next move is to debug code that is not running.

That happened in the sibling repository, token-price-index: the chart was
rewritten and the stamp was not bumped, so the site served the old chart while
the repository contained the new one. Here it was worse. style.css carried a
stamp typed by hand, and app.js carried none at all -- imported as "./app.js",
it was cached by every browser that had ever loaded the page, and there was no
stamp to forget to bump.

Run with ``--check`` in CI: it fails if any stamp disagrees with the file it
names, which makes the mistake impossible to ship rather than merely unlikely.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

CRLF, CR, LF = chr(13) + chr(10), chr(13), chr(10)

WEB = Path(__file__).resolve().parents[1] / "web"
ASSETS = ("app.js", "style.css")
PAGES = ("index.html", "method.html")


def digest(name: str) -> str:
    """Hash the file's content, not its line endings.

    Git checks out CRLF on Windows and LF on Linux, so hashing raw bytes makes
    the stamp machine-dependent: it matches locally, fails in CI, and the file
    is identical in both places. Normalise first. This guard caught it on its
    own first run in the sibling repository, which is the only reason it is
    written down here.
    """
    # newline="" keeps the bytes as written; Path.read_text only grew that
    # argument in 3.13 and this package supports 3.11.
    with (WEB / name).open(encoding="utf-8", newline="") as handle:
        raw = handle.read()
    text = raw.replace(CRLF, LF).replace(CR, LF)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def stamped(source: str, name: str, value: str) -> str:
    pattern = rf"{re.escape(name)}\?v=[0-9a-f]+"
    if re.search(pattern, source):
        return re.sub(pattern, f"{name}?v={value}", source)
    return source.replace(name, f"{name}?v={value}")


def main(check: bool) -> int:
    digests = {name: digest(name) for name in ASSETS}
    stale: list[str] = []

    for page in PAGES:
        path = WEB / page
        source = path.read_text(encoding="utf-8")
        wanted = source
        for name, value in digests.items():
            wanted = stamped(wanted, name, value)
        if wanted == source:
            continue
        if check:
            stale.append(page)
        else:
            path.write_text(wanted, encoding="utf-8")
            print(f"stamped {page}")

    if check and stale:
        print("asset stamps are stale in: " + ", ".join(stale))
        print("run: uv run python scripts/stamp_assets.py")
        print("the site would serve a cached copy of a file this repo has changed")
        return 1
    if check:
        print("asset stamps match their files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
