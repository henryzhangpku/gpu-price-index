"""Render web/og.png, the card LinkedIn, GitHub and Slack show when the site is linked.

Rasterised here rather than screenshotted because a screenshot is a manual step that
silently goes stale: the copy below is the only copy, and re-running the script is the
only way the image changes.  Drawn at SUPERSAMPLE x and downsampled, because Pillow does
not antialias -- at 1x the rounded ends of the mark come out visibly stepped.

    uv run python scripts/make_og.py

Fonts are fetched from Google Fonts on first run and cached in scripts/.fonts/.
"""

from __future__ import annotations

import io
import pathlib
import urllib.request

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- configuration

WIDTH, HEIGHT = 1200, 630
#: Pillow has no antialiasing, so everything is drawn large and resampled down.
SUPERSAMPLE = 3

BG = "#F5F7F7"
INK = "#101617"
MUTED = "#677577"
RULE = "#D9E0E0"
ACCENT = "#0E7C86"
PUBLISHED = "#17795E"
WITHHELD = "#94640C"

WORDMARK = "GPUIDX"
TITLE = "GPU Rental Price Benchmark"
LEDE = "Built the way a settlement index has to be built, not the way a dashboard can be."
URL = "henryzhangpku.github.io/gpu-price-index"
STATS = [
    ("5", "indices", INK),
    ("~20", "clouds", INK),
    ("3", "published", PUBLISHED),
    ("2", "withheld", WITHHELD),
]

#: The mark: three seller quotes at three heights, because no two GPU providers quote
#: alike.  Its sibling in token-price-index draws two of them level, which is that
#: repository's central finding.  (x, y, w, h) on a 32-unit grid, matching favicon.svg.
MARK_BARS = [(6, 13, 5, 13), (13.5, 7, 5, 19), (21, 17, 5, 9)]
MARK_STYLE = "solid"  # solid ground, knocked-out bars

FONTS = {
    "title": ("Chivo:wght@700", 72),
    "lede": ("Newsreader:wght@400", 33),
    "wordmark": ("IBM+Plex+Mono:wght@500", 23),
    "stat": ("IBM+Plex+Mono:wght@500", 27),
    "label": ("IBM+Plex+Mono:wght@500", 15),
    "url": ("IBM+Plex+Mono:wght@400", 20),
}

PAD_X, PAD_TOP, PAD_BOTTOM = 72, 64, 64
CACHE = pathlib.Path(__file__).parent / ".fonts"

# ---------------------------------------------------------------- font loading


def load(spec: str, size: int) -> ImageFont.FreeTypeFont:
    """Fetch a Google Font TTF once, then load it at `size` scaled for supersampling."""
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / (spec.replace(":", "-").replace("@", "-").replace("+", "") + ".ttf")
    if not cached.exists():
        # An old user agent is what makes the API serve TTF rather than WOFF2, which
        # Pillow cannot read.
        req = urllib.request.Request(
            f"https://fonts.googleapis.com/css2?family={spec}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 6.1)"},
        )
        css = urllib.request.urlopen(req).read().decode()
        url = css.split("url(")[1].split(")")[0]
        cached.write_bytes(urllib.request.urlopen(url).read())
    return ImageFont.truetype(io.BytesIO(cached.read_bytes()), size * SUPERSAMPLE)


# ---------------------------------------------------------------- drawing helpers


def px(value: float) -> int:
    return round(value * SUPERSAMPLE)


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
         max_width: int) -> list[str]:
    lines: list[str] = []
    words = text.split()
    line = ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= px(max_width) or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_mark(draw: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    """The favicon mark, scaled from its 32-unit grid to `size` pixels."""
    u = size / 32.0
    if MARK_STYLE == "solid":
        ground, bar = ACCENT, BG
        draw.rounded_rectangle(
            [px(x), px(y), px(x + size), px(y + size)], radius=px(7 * u), fill=ground
        )
    else:
        inset, stroke = 1.25 * u, 2.5 * u
        draw.rounded_rectangle(
            [px(x + inset), px(y + inset), px(x + size - inset), px(y + size - inset)],
            radius=px(6 * u), fill="#FFFFFF", outline=ACCENT, width=px(stroke),
        )
        bar = ACCENT
    for bx, by, bw, bh in MARK_BARS:
        draw.rounded_rectangle(
            [px(x + bx * u), px(y + by * u), px(x + (bx + bw) * u), px(y + (by + bh) * u)],
            radius=px(bw * u / 2), fill=bar,
        )


# ---------------------------------------------------------------- composition


def main() -> None:
    image = Image.new("RGB", (WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE), BG)
    draw = ImageDraw.Draw(image)
    f = {name: load(spec, size) for name, (spec, size) in FONTS.items()}

    # Header: mark, then the package name it publishes under.
    draw_mark(draw, PAD_X, PAD_TOP, 72)
    draw.text((px(PAD_X + 72 + 22), px(PAD_TOP + 36)), WORDMARK, font=f["wordmark"],
              fill=ACCENT, anchor="lm")

    # Footer is laid out bottom-up so the rule sits a fixed distance above it.
    label_h = 15 * 1.2
    stat_h = 27 * 1.2
    footer_top = HEIGHT - PAD_BOTTOM - (stat_h + 4 + label_h)
    rule_y = footer_top - 22
    draw.rectangle([px(PAD_X), px(rule_y), px(WIDTH - PAD_X), px(rule_y) + SUPERSAMPLE],
                   fill=RULE)

    x = PAD_X
    for value, label, colour in STATS:
        draw.text((px(x), px(footer_top)), value, font=f["stat"], fill=colour)
        draw.text((px(x), px(footer_top + stat_h + 4)), label.upper(), font=f["label"],
                  fill=MUTED)
        x += max(draw.textlength(value, font=f["stat"]),
                 draw.textlength(label.upper(), font=f["label"])) / SUPERSAMPLE + 34
    draw.text((px(WIDTH - PAD_X), px(HEIGHT - PAD_BOTTOM)), URL, font=f["url"],
              fill=MUTED, anchor="rs")

    # The block between header and rule, laid out upward from the rule.
    title_lines = wrap(draw, TITLE, f["title"], 780)
    lede_lines = wrap(draw, LEDE, f["lede"], 640)
    title_lh, lede_lh = 72 * 1.02, 33 * 1.34
    block_h = len(title_lines) * title_lh + 22 + len(lede_lines) * lede_lh
    top = rule_y - 30 - block_h

    for i, line in enumerate(title_lines):
        draw.text((px(PAD_X), px(top + i * title_lh)), line, font=f["title"], fill=INK)
    lede_top = top + len(title_lines) * title_lh + 22
    for i, line in enumerate(lede_lines):
        draw.text((px(PAD_X), px(lede_top + i * lede_lh)), line, font=f["lede"],
                  fill=MUTED)

    out = pathlib.Path(__file__).parent.parent / "web" / "og.png"
    image.resize((WIDTH, HEIGHT), Image.LANCZOS).save(out, optimize=True)
    print(f"{out.relative_to(out.parent.parent)}  {WIDTH}x{HEIGHT}  {out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
