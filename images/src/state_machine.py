# ruff: noqa: E501
"""Drift state machine: the six states and the events or actions that move a container between them."""

import math

from draw import CYAN, MINT, ORANGE, STATUS_ACCENT, Canvas

W, H = 1600, 920
c = Canvas(W, H, "DriftWarden drift states and the transitions between them")
c.title("Drift states")

NH = 90
pos = {
    "MATCHED": (220, 470),
    "DRIFTED": (720, 230),
    "MISSING": (720, 470),
    "ROGUE": (720, 710),
    "PENDING_ADOPTION": (1240, 470),
    "AWAITING_RESTART": (220, 830),
}
width = {n: (330 if len(n) > 8 else 260) for n in pos}


def trim(a, b, perp=0):
    """Endpoints of the edge a -> b, cut at the node borders and shifted sideways by `perp`."""
    (px, py), (qx, qy) = pos[a], pos[b]
    dx, dy = qx - px, qy - py
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L

    def cut(n, x, y, sx, sy):
        hw, hh = width[n] / 2 + 8, NH / 2 + 8
        t = min(hw / abs(sx) if sx else math.inf, hh / abs(sy) if sy else math.inf)
        return x + sx * t, y + sy * t

    sx, sy = cut(a, px, py, ux, uy)
    ex, ey = cut(b, qx, qy, -ux, -uy)
    nx, ny = -uy * perp, ux * perp
    return (sx + nx, sy + ny), (ex + nx, ey + ny)


def edge(a, b, color, perp=0, dash=None):
    s, e = trim(a, b, perp)
    c.arrow([s, e], stroke=color, sw=4, dash=dash)


# MATCHED <-> DRIFTED and MATCHED <-> MISSING
edge("MATCHED", "DRIFTED", ORANGE, perp=-14)
c.label(430, 300, "field changed", "orange")
edge("DRIFTED", "MATCHED", MINT, perp=-14)
c.label(565, 392, "Enforce", "mint")
edge("MATCHED", "MISSING", ORANGE, perp=-14)
c.label(470, 440, "stopped or removed", "orange")
edge("MISSING", "MATCHED", MINT, perp=-14)
c.label(470, 522, "Enforce", "mint")

# ROGUE comes from outside Git and leaves when Enforce removes it
c.arrow([(400, 694), (578, 694)], stroke=ORANGE, sw=4)
c.label(490, 672, "docker run", "orange")
c.arrow([(578, 728), (400, 728)], stroke=MINT, sw=4)
c.label(490, 768, "Enforce removes it", "mint")

# Adopt into the buffer, and back out when the pull request does not land
for n in ("DRIFTED", "MISSING", "ROGUE"):
    edge(n, "PENDING_ADOPTION", CYAN, perp=-12)
    edge("PENDING_ADOPTION", n, CYAN, perp=-12, dash="10 10")
c.label(930, 432, "Adopt → pull request", "cyan")
c.label(930, 520, "PR closed", "cyan")

# PR merged
c.path("M1240,417 L1240,140 L220,140 L220,417", stroke=MINT, sw=4)
c.label(730, 148, "PR merged", "mint")
c.path("M1240,523 L1240,830 L393,830", stroke=CYAN, sw=4)
c.label(840, 838, "PR merged for a ROGUE: Git declares it, no Compose labels yet", "cyan")
c.arrow([(220, 777), (220, 523)], stroke=MINT, sw=4)
c.label(220, 650, "restarted with labels", "mint")

for n, (x, y) in pos.items():
    c.node(x, y, width[n], NH, n, STATUS_ACCENT[n], size=26, mono=True)
c.save("state-machine")
