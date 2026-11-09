# ruff: noqa: E501
"""Drift state machine: the six states and the events or actions that move a container between them.

Sized for a projector (1920x1080). Two rows: DRIFTED, MATCHED, ROGUE on top and MISSING,
PENDING_ADOPTION, AWAITING_RESTART below. Every arrow takes the colour of the state it
arrives in, and its label runs along the arrow.
"""

import math

from draw import STATUS_ACCENT, Canvas, color_of

W, H = 1920, 1080
c = Canvas(W, H, "DriftWarden drift states and the transitions between them")
c.title("Drift states")

NH = 110
TOP, BOTTOM = 330, 800
pos = {
    "DRIFTED": (400, TOP),
    "MATCHED": (960, TOP),
    "ROGUE": (1520, TOP),
    "MISSING": (400, BOTTOM),
    "PENDING_ADOPTION": (960, BOTTOM),
    "AWAITING_RESTART": (1520, BOTTOM),
}
width = {n: (380 if len(n) > 8 else 300) for n in pos}
col = {n: color_of(STATUS_ACCENT[n]) for n in pos}
MINT = col["MATCHED"]
VIOLET = col["PENDING_ADOPTION"]


def trim(a, b, perp=0):
    """Endpoints of the edge a -> b, cut at the node borders and shifted sideways by `perp`."""
    (px, py), (qx, qy) = pos[a], pos[b]
    dx, dy = qx - px, qy - py
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L

    def cut(n, x, y, sx, sy):
        hw, hh = width[n] / 2 + 10, NH / 2 + 10
        t = min(hw / abs(sx) if sx else math.inf, hh / abs(sy) if sy else math.inf)
        return x + sx * t, y + sy * t

    sx, sy = cut(a, px, py, ux, uy)
    ex, ey = cut(b, qx, qy, -ux, -uy)
    nx, ny = -uy, ux
    return (sx + nx * perp, sy + ny * perp), (ex + nx * perp, ey + ny * perp), (nx, ny), (ux, uy)


def edge(a, b, text, color, perp=0, dash=None, gap=36):
    """Arrow a -> b with its label running along the line, pushed `gap` px to the side of `perp`."""
    s, e, (nx, ny), (ux, uy) = trim(a, b, perp)
    c.arrow([s, e], stroke=color, sw=4, dash=dash)
    side = 1 if perp >= 0 else -1
    mx, my = (s[0] + e[0]) / 2 + nx * gap * side, (s[1] + e[1]) / 2 + ny * gap * side
    angle = math.degrees(math.atan2(uy, ux))
    if angle > 90 or angle < -90:
        angle += 180  # keep the words upright
    if abs(abs(angle) - 90) < 1:
        angle = -90  # vertical arrows read bottom to top
    c.label(mx, my, text, color, angle=angle)


# top row: MATCHED <-> DRIFTED
edge("MATCHED", "DRIFTED", "field changed", col["DRIFTED"], perp=16)
edge("DRIFTED", "MATCHED", "Enforce", MINT, perp=16)
# MATCHED <-> MISSING, diagonal
edge("MATCHED", "MISSING", "stopped / removed", col["MISSING"], perp=16)
edge("MISSING", "MATCHED", "Enforce", MINT, perp=16)
# bottom row: MISSING <-> PENDING_ADOPTION
edge("PENDING_ADOPTION", "MISSING", "PR closed", col["MISSING"], perp=16, dash="10 10")
edge("MISSING", "PENDING_ADOPTION", "Adopt", VIOLET, perp=16)
# ROGUE <-> PENDING_ADOPTION, diagonal
edge("PENDING_ADOPTION", "ROGUE", "PR closed", col["ROGUE"], perp=-16, dash="10 10")
edge("ROGUE", "PENDING_ADOPTION", "Adopt", VIOLET, perp=-16)
# PENDING_ADOPTION -> MATCHED and -> AWAITING_RESTART
edge("PENDING_ADOPTION", "MATCHED", "PR merged", MINT, gap=-36)
edge(
    "PENDING_ADOPTION", "AWAITING_RESTART", "PR merged for a ROGUE", col["AWAITING_RESTART"], gap=82
)

# DRIFTED <-> PENDING_ADOPTION, looped around the left: Adopt outside, PR closed inside
c.path(
    f"M240,{TOP + 16} L70,{TOP + 16} L70,1020 L1000,1020 L1000,{BOTTOM + 65}", stroke=VIOLET, sw=4
)
c.label(70, 640, "Adopt", "violet", angle=-90)
c.path(
    f"M920,{BOTTOM + 65} L920,970 L130,970 L130,{TOP - 16} L240,{TOP - 16}",
    stroke=col["DRIFTED"],
    sw=4,
    dash="10 10",
)
c.label(130, 560, "PR closed", "amber", angle=-90)

# AWAITING_RESTART -> MATCHED, looped over the top
c.path(f"M1720,{BOTTOM} L1850,{BOTTOM} L1850,170 L960,170 L960,{TOP - 65}", stroke=MINT, sw=4)
c.label(1850, 540, "restarted with Compose labels", "mint", angle=-90)

for n, (x, y) in pos.items():
    c.node(x, y, width[n], NH, n, STATUS_ACCENT[n], size=30, mono=True)
c.save("state-machine")
