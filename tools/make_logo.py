#!/usr/bin/env python3
"""
Regenerate the logo files in docs/logo/:

    pip install fonttools
    npm pack @fontsource/jetbrains-mono && tar -xzf fontsource-jetbrains-mono-*.tgz
    python tools/make_logo.py package/files/jetbrains-mono-latin-800-normal.woff

The mark is an instrument screen: a flat teal trace (counts that never wobble)
that steps up in amber, the one change the gate exists to catch. The wordmark
is JetBrains Mono ExtraBold (SIL Open Font License 1.1), converted to outlines
so it renders the same everywhere, with no font to load.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "logo"

# light/dark: the wordmark's colours; the screen tile is the same on both
THEMES = {
    "light": {"ink": "#111827", "accent": "#D97706"},
    "dark": {"ink": "#F3F4F6", "accent": "#FBBF24"},
}


def screen() -> str:
    """Dark tile with an edge (so it holds on dark pages), faint grid, the trace."""
    grid = "".join(
        f'<line x1="{v}" y1="8" x2="{v}" y2="56"/><line x1="8" y1="{v}" x2="56" y2="{v}"/>'
        for v in (20, 32, 44)
    )
    return (
        '<rect x=".75" y=".75" width="62.5" height="62.5" rx="13.5" fill="#0B1220" '
        'stroke="#334155" stroke-width="1.5"/>'
        f'<g stroke="#E5E7EB" stroke-width=".8" opacity=".14">{grid}</g>'
        '<polyline points="8,40 33,40" fill="none" stroke="#2DD4BF" stroke-width="5" '
        'stroke-linecap="round"/>'
        '<polyline points="33,40 33,22 56,22" fill="none" stroke="#FBBF24" stroke-width="5" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def wordmark(font: TTFont, text: str, x: float, baseline: float, size: float,
             ink: str, accent: str) -> tuple[str, float]:
    """Outlined text, the x in the accent colour; returns (svg, width)."""
    cmap, glyphs = font.getBestCmap(), font.getGlyphSet()
    scale = size / font["head"].unitsPerEm
    parts, cursor = [], x
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, baseline)))
        parts.append(f'<path d="{pen.getCommands()}" fill="{accent if ch == "x" else ink}"/>')
        cursor += font["hmtx"][name][0] * scale
    return "".join(parts), cursor - x


def main(font_path: str) -> int:
    font = TTFont(font_path)
    OUT.mkdir(parents=True, exist_ok=True)
    svg = 'xmlns="http://www.w3.org/2000/svg"'

    (OUT / "icon.svg").write_text(
        f'<svg {svg} viewBox="0 0 64 64" width="512" height="512">{screen()}</svg>\n')

    for name, t in THEMES.items():
        words, width = wordmark(font, "pyxtrace", 76, 45, 40, t["ink"], t["accent"])
        (OUT / f"logo-{name}.svg").write_text(
            f'<svg {svg} viewBox="0 0 {76 + width + 4:.1f} 64" height="96" '
            f'role="img" aria-label="pyxtrace">{screen()}{words}</svg>\n')

    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    raise SystemExit(main(sys.argv[1]))
