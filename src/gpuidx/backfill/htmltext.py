"""Reduce an archived HTML page to the text cells a reader would see.

Rate-card pages change their markup every few months -- tables become card
grids, grids become React components -- while the *reading order* of the text
on the page is far more stable: a GPU name, then its specs, then a price.
Parsers therefore work on a flat list of text cells rather than on the DOM,
which keeps each one to a few patterns and makes a parser's assumptions easy
to read and to test against saved fixtures.

Standard library only (``html.parser``); no new dependency.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "svg", "template", "head"}
_BLOCK = {
    "p", "div", "li", "td", "th", "tr", "br", "h1", "h2", "h3", "h4", "h5",
    "h6", "span", "a", "button", "section", "article", "dt", "dd", "label",
    "strong", "b", "em", "small", "sup", "sub", "table", "ul", "ol",
}


class _Cells(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self._buf: list[str] = []
        self._skip = 0

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        if text:
            self.cells.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def cells(raw: bytes | str) -> list[str]:
    """The page's visible text, one entry per block-level cell, in order."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    parser = _Cells()
    parser.feed(text)
    parser.close()
    return parser.cells


def flat(raw: bytes | str) -> str:
    """The cells joined with `` | ``, for regex parsers that span cells."""
    return " | ".join(cells(raw))


def scripts(raw: bytes | str) -> list[str]:
    """Inline script bodies, for pages whose prices live in embedded JSON."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    return [html.unescape(m) for m in re.findall(r"<script[^>]*>(.*?)</script>", text, flags=re.S)]


_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)")


def money(text: str) -> float | None:
    """The first dollar amount in ``text``, or None."""
    m = _MONEY.search(text)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def all_money(text: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) for m in _MONEY.finditer(text)]
