# ruff: noqa: E501
"""Drift state machine."""

import math

from draw import BORDER, CARD, DIM, MUTED, STATUS_TONE, TEXT, TONES, Canvas

W, H = 1600, 1000
c = Canvas(W, H, "DriftWarden drift states and the transitions between them")
c.header(
    "drift states",
    "How a container moves between states; Enforce and Adopt are the only operator verbs",
)

NW, NH = 236, 72
pos = {
    "MATCHED": (800, 170),
    "MISSING": (220, 480),
    "DRIFTED": (1380, 480),
    "AWAITING_RESTART": (220, 800),
    "PENDING_ADOPTION": (800, 800),
    "ROGUE": (1380, 800),
}
sub = {
    "MATCHED": "live container equals its baseline service",
    "MISSING": "declared in Git, no running container",
    "DRIFTED": "a compared field or the runtime state differs",
    "AWAITING_RESTART": "PR merged, still running without labels",
    "PENDING_ADOPTION": "in the buffer while its PR is open",
    "ROGUE": "running, but Git declares nothing",
}
ROSE, EM, AM, SKY, VIO, OR = (
    TONES[t][1] for t in ("rose", "emerald", "amber", "sky", "violet", "orange")
)


def node(name):
    x, y = pos[name]
    fill, stroke, color = TONES[STATUS_TONE[name]]
    c.rect(x - NW / 2, y - NH / 2, NW, NH, fill=fill, stroke=stroke, rx=14, sw=2)
    c.text(x, y - 6, name, size=15, fill=color, weight=800, anchor="middle", mono=True)
    c.text(x, y + 16, sub[name], size=10.5, fill=TEXT, anchor="middle", opacity=0.85)


def trim(px, py, qx, qy):
    dx, dy = qx - px, qy - py
    sx = (NW / 2 + 6) / abs(dx) if dx else math.inf
    sy = (NH / 2 + 6) / abs(dy) if dy else math.inf
    s = min(sx, sy)
    return px + dx * s, py + dy * s


def edge(a, b, color, perp=0, dash=None):
    (x1, y1), (x2, y2) = pos[a], pos[b]
    sx, sy = trim(x1, y1, x2, y2)
    ex, ey = trim(x2, y2, x1, y1)
    dx, dy = ex - sx, ey - sy
    L = math.hypot(dx, dy)
    nx, ny = -dy / L * perp, dx / L * perp
    c.arrow([(sx + nx, sy + ny), (ex + nx, ey + ny)], stroke=color, sw=2.2, dash=dash)


def lab(x, y, lines, color):
    for i, s in enumerate(lines):
        c.label(x, y + i * 15, s, size=11.5, fill=color)


# MATCHED <-> DRIFTED
edge("MATCHED", "DRIFTED", AM, perp=-10)
lab(1000, 225, ["field changed, or the container is", "paused / restarting / dead"], AM)
edge("DRIFTED", "MATCHED", ROSE, perp=-10)
lab(1140, 410, ["Enforce: recreate from the baseline", "(or the baseline changes to agree)"], ROSE)
# MATCHED <-> MISSING
edge("MATCHED", "MISSING", SKY, perp=10)
lab(600, 225, ["container stopped,", "exited or removed"], SKY)
edge("MISSING", "MATCHED", ROSE, perp=10)
lab(440, 410, ["Enforce: create from the baseline", "(or docker compose up)"], ROSE)
# into the buffer
edge("DRIFTED", "PENDING_ADOPTION", EM, perp=-10)
lab(1050, 590, ["Adopt: PR syncs only", "the changed fields"], EM)
edge("MISSING", "PENDING_ADOPTION", EM, perp=10)
lab(550, 590, ["Adopt: PR removes", "the service block"], EM)
edge("ROGUE", "PENDING_ADOPTION", EM, perp=-11)
lab(
    1070,
    728,
    ["Adopt: PR adds a service block", "(env values become ${KEY}, no container_name)"],
    EM,
)
# out of the buffer
edge("PENDING_ADOPTION", "DRIFTED", VIO, perp=-10, dash="6 5")
edge("PENDING_ADOPTION", "MISSING", VIO, perp=10, dash="6 5")
edge("PENDING_ADOPTION", "ROGUE", VIO, perp=-11, dash="6 5")
lab(
    1090,
    860,
    [
        "PR closed unmerged, Revoke Buffer, or the TTL",
        "while the PR state is unknown:",
        "back to the state it came from (all three dashed arrows)",
    ],
    VIO,
)
edge("PENDING_ADOPTION", "AWAITING_RESTART", OR)
lab(
    510,
    860,
    ["PR merged: Git now declares the adopted", "rogue, but the container has no Compose labels"],
    OR,
)
edge("PENDING_ADOPTION", "MATCHED", EM)
lab(
    950,
    500,
    ["PR merged: baseline re-fetched, the drifted", "service or the deletion now agrees"],
    EM,
)
# AWAITING_RESTART -> MATCHED around the left edge
ax, ay = pos["AWAITING_RESTART"]
c.path(
    f"M{ax - NW / 2 - 6},{ay} L60,{ay} Q40,{ay} 40,{ay - 20} L40,190 Q40,170 60,170 L{800 - NW / 2 - 6},170",
    stroke=ROSE,
    sw=2.2,
)
lab(
    360, 150, ["Enforce, or docker compose up on the host: recreated with its Compose labels"], ROSE
)

# ROGUE entry and exit
rx, ry = pos["ROGUE"]
c.arrow([(rx, ry - 120), (rx, ry - NH / 2 - 6)], stroke=ROSE, sw=2.2)
lab(
    rx,
    ry - 150,
    ["docker run, foreign Compose project,", "spoofed labels, duplicate replica"],
    ROSE,
)
c.arrow([(rx, ry + NH / 2 + 6), (rx, ry + 100)], stroke=ROSE, sw=2.2)
lab(rx, ry + 122, ["Enforce: stop, then remove"], ROSE)

for n in pos:
    node(n)

c.rect(60, 905, 620, 70, fill=CARD, stroke=BORDER)
c.text(80, 930, "Enforce mode", size=13, fill=TEXT, weight=700)
c.lines(
    80,
    950,
    [
        "After the grace window, Enforce runs by itself on DRIFTED, MISSING, ROGUE and AWAITING_RESTART.",
        "PENDING_ADOPTION, whitelisted and excluded entries are never touched.",
    ],
    size=11.5,
    fill=MUTED,
)
c.text(60, 118, "solid: operator action or host event", size=11.5, fill=DIM)
c.text(60, 135, "dashed: the pull request did not land", size=11.5, fill=DIM)
c.footer("src/shared/constants.py · DriftStatus")
c.save("state-machine")
