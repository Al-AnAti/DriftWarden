# ruff: noqa: E501
"""Drift state machine: the six states and the events or actions that move a container between them.

Sized for a projector (1920x1080). Every arrow takes the colour of the state it arrives in;
labels run along their arrows.
"""

import math

from draw import STATUS_ACCENT, Canvas, color_of

W, H = 1920, 1080
c = Canvas(W, H, "DriftWarden drift states and the transitions between them")
c.title("Drift states")

NH = 100
pos = {
    "MATCHED": (960, 200),
    "DRIFTED": (420, 580),
    "MISSING": (960, 580),
    "ROGUE": (1500, 580),
    "PENDING_ADOPTION": (960, 920),
    "AWAITING_RESTART": (1500, 920),
}
width = {n: (380 if len(n) > 8 else 300) for n in pos}
col = {n: color_of(STATUS_ACCENT[n]) for n in pos}
MINT = col["MATCHED"]


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
    """Arrow a -> b with its label running along the line, pushed `gap` px to the side."""
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


# leaving MATCHED (host event) and coming back (Enforce)
edge("MATCHED", "DRIFTED", "field changed", col["DRIFTED"], perp=-16)
edge("DRIFTED", "MATCHED", "Enforce", MINT, perp=-16)
edge("MATCHED", "MISSING", "stopped / removed", col["MISSING"], perp=-16)
edge("MISSING", "MATCHED", "Enforce", MINT, perp=-16)

# Adopt into the buffer, and back out when the pull request does not land
for n in ("DRIFTED", "MISSING", "ROGUE"):
    edge(n, "PENDING_ADOPTION", "Adopt", col["PENDING_ADOPTION"], perp=-16)
    edge("PENDING_ADOPTION", n, "PR closed", col["PENDING_ADOPTION"], perp=-16, dash="10 10")

# PR merged: straight back to MATCHED (left loop), or a restart first for an adopted ROGUE
c.path("M760,920 L160,920 L160,200 L800,200", stroke=MINT, sw=4)
c.label(160, 560, "PR merged", "mint", angle=-90)
edge(
    "PENDING_ADOPTION", "AWAITING_RESTART", "PR merged for a ROGUE", col["AWAITING_RESTART"], gap=88
)
c.path("M1700,920 L1760,920 L1760,200 L1120,200", stroke=MINT, sw=4)
c.label(1760, 560, "restarted with Compose labels", "mint", angle=-90)

for n, (x, y) in pos.items():
    c.node(x, y, width[n], NH, n, STATUS_ACCENT[n], size=30, mono=True)
c.save("state-machine")
