# ruff: noqa: E501
"""Tiny SVG drawing helper shared by the DriftWarden diagram generators."""

from __future__ import annotations

import html

FONT = "Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'Cascadia Mono', Consolas, Menlo, monospace"

BG = "#0b1120"
CARD = "#111a2e"
CARD2 = "#172034"
BORDER = "#263247"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
DIM = "#64748b"
INDIGO = "#818cf8"

# tone -> (fill, stroke, text) taken from the dashboard's .tone-* classes
TONES = {
    "emerald": ("rgba(6,78,59,0.55)", "#10b981", "#6ee7b7"),
    "amber": ("rgba(120,53,15,0.55)", "#f59e0b", "#fcd34d"),
    "sky": ("rgba(12,74,110,0.55)", "#0ea5e9", "#7dd3fc"),
    "rose": ("rgba(136,19,55,0.55)", "#f43f5e", "#fda4af"),
    "violet": ("rgba(76,29,149,0.55)", "#8b5cf6", "#c4b5fd"),
    "orange": ("rgba(124,45,18,0.55)", "#f97316", "#fdba74"),
    "slate": ("rgba(30,41,59,0.8)", "#64748b", "#cbd5e1"),
    "indigo": ("rgba(49,46,129,0.55)", "#818cf8", "#c7d2fe"),
    "teal": ("rgba(19,78,74,0.55)", "#14b8a6", "#5eead4"),
}
STATUS_TONE = {
    "MATCHED": "emerald",
    "DRIFTED": "amber",
    "MISSING": "sky",
    "ROGUE": "rose",
    "PENDING_ADOPTION": "violet",
    "AWAITING_RESTART": "orange",
}


def esc(s: str) -> str:
    return html.escape(s, quote=True)


class Canvas:
    def __init__(self, w: int, h: int, title: str, bg: str | None = BG) -> None:
        self.w, self.h, self.title = w, h, title
        self.parts: list[str] = []
        self.markers: set[str] = set()
        self.bg = bg

    # ---- primitives -------------------------------------------------
    def raw(self, s: str) -> None:
        self.parts.append(s)

    def rect(self, x, y, w, h, fill=CARD, stroke=BORDER, rx=12, sw=1.5, dash=None, opacity=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' opacity="{opacity}"' if opacity is not None else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{extra}/>'
        )

    def circle(self, cx, cy, r, fill, stroke="none", sw=1.5):
        self.parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )

    def text(
        self,
        x,
        y,
        s,
        size=14,
        fill=TEXT,
        weight=400,
        anchor="start",
        mono=False,
        italic=False,
        spacing=None,
        opacity=None,
    ):
        fam = MONO if mono else FONT
        extra = ' font-style="italic"' if italic else ""
        extra += f' letter-spacing="{spacing}"' if spacing else ""
        extra += f' opacity="{opacity}"' if opacity is not None else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{esc(s)}</text>'
        )

    def lines(
        self, x, y, items, size=13, fill=MUTED, lh=None, anchor="start", mono=False, weight=400
    ):
        lh = lh or round(size * 1.45)
        for i, s in enumerate(items):
            self.text(
                x, y + i * lh, s, size=size, fill=fill, anchor=anchor, mono=mono, weight=weight
            )
        return y + len(items) * lh

    def _marker(self, color: str) -> str:
        mid = "m" + color.strip("#").replace("(", "").replace(")", "").replace(",", "").replace(
            ".", ""
        )
        self.markers.add(
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
        )
        return mid

    def path(
        self, d, stroke=MUTED, sw=2, head=True, dash=None, fill="none", tail=False, opacity=None
    ):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' opacity="{opacity}"' if opacity is not None else ""
        if head:
            extra += f' marker-end="url(#{self._marker(stroke)})"'
        if tail:
            extra += f' marker-start="url(#{self._marker(stroke)})"'
        self.parts.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round"{extra}/>'
        )

    def arrow(self, pts, stroke=MUTED, sw=2, head=True, dash=None, tail=False):
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        self.path(d, stroke=stroke, sw=sw, head=head, dash=dash, tail=tail)

    def label(self, x, y, s, size=12, fill=MUTED, anchor="middle", mono=False, pad=6, bg=BG):
        """Text with a background plate so it can sit on top of a line."""
        w = est_width(s, size, mono) + pad * 2
        self.parts.append(
            f'<rect x="{x - (w / 2 if anchor == "middle" else (0 if anchor == "start" else w))}" '
            f'y="{y - size}" width="{w}" height="{size + 8}" rx="4" fill="{bg}"/>'
        )
        self.text(x, y, s, size=size, fill=fill, anchor=anchor, mono=mono)

    # ---- compound ---------------------------------------------------
    def pill(self, x, y, s, tone="slate", size=11, h=22, anchor="start", mono=False, weight=700):
        fill, stroke, color = TONES[tone]
        w = est_width(s, size, mono, weight) + 20
        if anchor == "middle":
            x = x - w / 2
        elif anchor == "end":
            x = x - w
        self.rect(x, y, w, h, fill=fill, stroke=stroke, rx=h / 2, sw=1.2)
        self.text(
            x + w / 2,
            y + h / 2 + size * 0.36,
            s,
            size=size,
            fill=color,
            weight=weight,
            anchor="middle",
            mono=mono,
            spacing=0.4,
        )
        return w

    def card(
        self,
        x,
        y,
        w,
        h,
        title=None,
        tone=None,
        lines=(),
        title_size=15,
        body_size=12.5,
        mono_body=False,
        fill=CARD,
        dash=None,
        body_fill=MUTED,
        title_fill=TEXT,
        pad=16,
    ):
        stroke = TONES[tone][1] if tone else BORDER
        self.rect(x, y, w, h, fill=fill, stroke=stroke, dash=dash)
        cy = y + pad + title_size * 0.8
        if title:
            self.text(x + pad, cy, title, size=title_size, fill=title_fill, weight=700)
            cy += title_size * 1.35
        if lines:
            self.lines(x + pad, cy + 2, lines, size=body_size, fill=body_fill, mono=mono_body)

    def header(self, title, subtitle=None, x=None, y=54, size=30):
        x = self.w / 2 if x is None else x
        anchor = "middle" if x == self.w / 2 else "start"
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="800" '
            f'text-anchor="{anchor}" fill="{TEXT}"><tspan fill="{INDIGO}">Drift</tspan>Warden '
            f'<tspan font-weight="600" fill="{TEXT}">{esc(title)}</tspan></text>'
        )
        if subtitle:
            self.text(x, y + 30, subtitle, size=15, fill=MUTED, anchor=anchor)

    def footer(self, s):
        self.text(self.w - 28, self.h - 20, s, size=11, fill=DIM, anchor="end")

    def svg(self) -> str:
        bg = f'<rect width="100%" height="100%" fill="{self.bg}"/>' if self.bg else ""
        defs = "<defs>" + "".join(sorted(self.markers)) + "</defs>"
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t">\n'
            f'<title id="t">{esc(self.title)}</title>\n{defs}\n{bg}\n'
            + "\n".join(self.parts)
            + "\n</svg>\n"
        )

    def save(self, name: str) -> None:
        with open(f"{name}.svg", "w", encoding="utf-8") as f:
            f.write(self.svg())
        print("wrote", name)


def est_width(s: str, size: float, mono=False, weight=400) -> float:
    per = 0.62 if mono else (0.58 if weight >= 700 else 0.54)
    return len(s) * size * per
