# ruff: noqa: E501
"""Tiny SVG drawing helper shared by the DriftWarden diagram generators.

House style for every diagram:

* minimalist: core components and primary flows only, no footnotes or secondary boxes;
* very large, bold type (nothing below 20 px, titles at 30 px and up);
* a dark slate background with three glowing accents: cyan, mint green and warm orange;
* plain hand-written SVG, so the files stay small, diffable and editable.
"""

from __future__ import annotations

import html
import re

FONT = "Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, 'Cascadia Mono', Consolas, Menlo, monospace"

BG = "#0f172a"  # slate-900
PANEL = "#1e293b"  # slate-800, neutral boxes
LINE = "#475569"  # slate-600, neutral strokes
TEXT = "#f8fafc"
MUTED = "#94a3b8"
DIM = "#64748b"

CYAN = "#22d3ee"
MINT = "#34d399"
ORANGE = "#fb923c"
SLATE = "#94a3b8"

ROSE = "#fb7185"
VIOLET = "#a78bfa"
AMBER = "#fbbf24"

ACCENT = {
    "cyan": CYAN,
    "mint": MINT,
    "orange": ORANGE,
    "slate": SLATE,
    "rose": ROSE,
    "violet": VIOLET,
    "amber": AMBER,
}

# One colour per drift state, matching the dashboard's status pills.
STATUS_ACCENT = {
    "MATCHED": "mint",
    "DRIFTED": "amber",
    "MISSING": "cyan",
    "ROGUE": "rose",
    "PENDING_ADOPTION": "violet",
    "AWAITING_RESTART": "orange",
}


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def tint(hex_color: str, alpha: float) -> str:
    """Turn a #rrggbb colour into an rgba() string with the given alpha."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def est_width(s: str, size: float, mono: bool = False, weight: int = 800) -> float:
    per = 0.62 if mono else (0.60 if weight >= 700 else 0.54)
    return len(s) * size * per


def color_of(accent: str) -> str:
    return ACCENT.get(accent, accent)


class Canvas:
    def __init__(self, w: int, h: int, title: str, bg: str | None = BG) -> None:
        self.w, self.h, self.title_text = w, h, title
        self.parts: list[str] = []
        self.defs: dict[str, str] = {}
        self.bg = bg

    # ---- defs -------------------------------------------------------
    def glow(self, color: str, blur: float = 10, opacity: float = 0.75) -> str:
        """Register a drop-shadow glow in the given colour; returns the filter attribute value."""
        fid = "g" + color.lstrip("#") + str(int(blur))
        self.defs[fid] = (
            f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="-100" y="-100" width="{self.w + 200}" height="{self.h + 200}">'
            f'<feDropShadow dx="0" dy="0" stdDeviation="{blur}" flood-color="{color}" flood-opacity="{opacity}"/>'
            f"</filter>"
        )
        return f"url(#{fid})"

    def marker(self, color: str) -> str:
        mid = "m" + color.lstrip("#")
        self.defs[mid] = (
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" '
            f'markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
        )
        return f"url(#{mid})"

    # ---- primitives -------------------------------------------------
    def raw(self, s: str) -> None:
        self.parts.append(s)

    def rect(
        self, x, y, w, h, fill=PANEL, stroke=LINE, rx=18, sw=3, dash=None, glow=None, opacity=None
    ):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' filter="{glow}"' if glow else ""
        extra += f' opacity="{opacity}"' if opacity is not None else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{extra}/>'
        )

    def circle(self, cx, cy, r, fill, stroke="none", sw=3, glow=None):
        extra = f' filter="{glow}"' if glow else ""
        self.parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{extra}/>'
        )

    def text(
        self,
        x,
        y,
        s,
        size=24,
        fill=TEXT,
        weight=800,
        anchor="start",
        mono=False,
        glow=None,
        spacing=None,
        opacity=None,
    ):
        fam = MONO if mono else FONT
        extra = f' filter="{glow}"' if glow else ""
        extra += f' letter-spacing="{spacing}"' if spacing else ""
        extra += f' opacity="{opacity}"' if opacity is not None else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{esc(s)}</text>'
        )

    def lines(
        self, x, y, items, size=22, fill=MUTED, lh=None, anchor="start", weight=600, mono=False
    ):
        lh = lh or round(size * 1.4)
        for i, s in enumerate(items):
            self.text(
                x, y + i * lh, s, size=size, fill=fill, weight=weight, anchor=anchor, mono=mono
            )
        return y + len(items) * lh

    def path(self, d, stroke=LINE, sw=4, head=True, tail=False, dash=None, glow=True, opacity=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' marker-end="{self.marker(stroke)}"' if head else ""
        extra += f' marker-start="{self.marker(stroke)}"' if tail else ""
        extra += f' filter="{self.glow(stroke, 6, 0.6)}"' if glow and stroke != LINE else ""
        extra += f' opacity="{opacity}"' if opacity is not None else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round"{extra}/>'
        )

    def arrow(self, pts, stroke=LINE, sw=4, head=True, tail=False, dash=None, glow=True):
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        self.path(d, stroke=stroke, sw=sw, head=head, tail=tail, dash=dash, glow=glow)

    # ---- compound ---------------------------------------------------
    def title(self, s, size=44, y=78, x=60, accent="cyan"):
        """Page title: a short glowing accent bar and one bold line."""
        col = color_of(accent)
        self.rect(
            x,
            y - size * 0.78,
            10,
            size * 0.9,
            fill=col,
            stroke="none",
            rx=5,
            glow=self.glow(col, 8),
        )
        self.text(x + 30, y, s, size=size, fill=TEXT, weight=800)

    def node(
        self,
        x,
        y,
        w,
        h,
        label,
        accent="cyan",
        sub=None,
        size=30,
        mono=False,
        dash=None,
        glow_blur=12,
    ):
        """A glowing rounded box centred on (x, y) with a bold label and an optional sub-line."""
        col = color_of(accent)
        self.rect(
            x - w / 2,
            y - h / 2,
            w,
            h,
            fill=tint(col, 0.10),
            stroke=col,
            rx=min(h / 4, 22),
            sw=3,
            dash=dash,
            glow=self.glow(col, glow_blur),
        )
        if sub:
            self.text(x, y + size * 0.05, label, size=size, fill=col, anchor="middle", mono=mono)
            self.text(
                x,
                y + size * 0.05 + 32,
                sub,
                size=20,
                fill=TEXT,
                weight=600,
                anchor="middle",
                opacity=0.9,
            )
        else:
            self.text(x, y + size * 0.36, label, size=size, fill=col, anchor="middle", mono=mono)

    def pill(
        self, x, y, s, accent="slate", size=22, h=None, anchor="middle", mono=False, glow=True
    ):
        """A glowing capsule with bold text; (x, y) is the capsule's centre."""
        col = color_of(accent)
        h = h or round(size * 1.9)
        w = est_width(s, size, mono) + size * 1.4
        if anchor == "start":
            x += w / 2
        elif anchor == "end":
            x -= w / 2
        self.rect(
            x - w / 2,
            y - h / 2,
            w,
            h,
            fill=tint(col, 0.14),
            stroke=col,
            rx=h / 2,
            sw=2.5,
            glow=self.glow(col, 8, 0.6) if glow else None,
        )
        self.text(
            x, y + size * 0.36, s, size=size, fill=col, anchor="middle", mono=mono, spacing=0.5
        )
        return w

    def label(self, x, y, s, accent=None, size=22, anchor="middle", weight=800, pad=10, angle=0):
        """Bold text on a background plate, for words that sit on top of a line.

        `angle` rotates the label about (x, y) so it can run along a diagonal arrow.
        """
        fill = color_of(accent) if accent else MUTED
        w = est_width(s, size) + pad * 2
        x0 = x - w / 2 if anchor == "middle" else (x - pad if anchor == "start" else x - w + pad)
        if angle:
            self.parts.append(f'<g transform="rotate({angle:.1f} {x} {y})">')
        self.rect(x0, y - size * 0.95, w, size * 1.35, fill=self.bg or BG, stroke="none", rx=8)
        self.text(x, y, s, size=size, fill=fill, weight=weight, anchor=anchor)
        if angle:
            self.parts.append("</g>")

    def svg(self) -> str:
        bg = f'<rect width="100%" height="100%" fill="{self.bg}"/>' if self.bg else ""
        defs = "<defs>" + "".join(self.defs[k] for k in sorted(self.defs)) + "</defs>"
        flat = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t">'
            f'<title id="t">{esc(self.title_text)}</title>{defs}{bg}'
            + "".join(self.parts)
            + "</svg>"
        )
        return indent_svg(flat)

    def save(self, name: str) -> None:
        with open(f"{name}.svg", "w", encoding="utf-8") as f:
            f.write(self.svg())
        print("wrote", name)


INLINE = {"text", "title", "tspan", "desc"}


def indent_svg(src: str, step: str = "  ") -> str:
    """Re-indent SVG markup, one element per line, keeping text-bearing elements on one line.

    Whitespace only: the rendered result is identical to the input.
    """
    tokens = re.findall(r"<[^>]+>|[^<]+", src)
    out: list[str] = []
    depth = 0
    inline = 0  # nesting depth inside a text-bearing element
    for tok in tokens:
        if not tok.startswith("<"):
            if inline:
                out[-1] += tok
            elif tok.strip():
                out.append(step * depth + tok.strip())
            continue
        name = re.match(r"</?([\w:-]+)", tok)
        name = name.group(1) if name else ""
        if tok.startswith("</"):
            if inline:
                out[-1] += tok
                inline -= 1
            else:
                depth = max(depth - 1, 0)
                out.append(step * depth + tok)
        elif tok.endswith("/>") or tok.startswith("<?") or tok.startswith("<!"):
            if inline:
                out[-1] += tok
            else:
                out.append(step * depth + tok)
        else:
            if inline or name in INLINE:
                if inline:
                    out[-1] += tok
                else:
                    out.append(step * depth + tok)
                inline += 1
            else:
                out.append(step * depth + tok)
                depth += 1
    return "\n".join(out) + "\n"
