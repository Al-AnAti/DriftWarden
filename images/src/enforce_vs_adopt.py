# ruff: noqa: E501
"""Enforce vs Adopt: the two directions."""

from draw import MINT, ORANGE, TEXT, Canvas, tint

W, H = 1600, 700
c = Canvas(
    W,
    H,
    "DriftWarden two actions, two directions: Enforce makes the host match Git, Adopt makes Git match the host",
)
c.title("Two actions, two directions")

c.node(250, 400, 320, 380, "Git", "cyan", sub="docker-compose.yaml", size=44)
c.node(1350, 400, 320, 380, "Docker host", "cyan", sub="the live containers", size=44)


def chevron(x1, x2, y, color, h=120):
    """A wide glowing arrow from x1 to x2 (direction by sign)."""
    d = 1 if x2 > x1 else -1
    tip = 60 * d
    pts = [(x1, y - h / 2), (x2 - tip, y - h / 2), (x2, y), (x2 - tip, y + h / 2), (x1, y + h / 2)]
    c.raw(
        '<polygon points="'
        + " ".join(f"{x},{y}" for x, y in pts)
        + f'" fill="{tint(color, 0.12)}" stroke="{color}" stroke-width="3" stroke-linejoin="round" filter="{c.glow(color, 12)}"/>'
    )


# Enforce: Git -> host
chevron(440, 1160, 300, ORANGE)
c.text(480, 292, "Enforce", size=40, fill=ORANGE)
c.text(660, 292, "make the host match Git", size=30, fill=TEXT, weight=700)
c.text(
    480,
    334,
    "recreate · create · remove, through the Docker SDK",
    size=22,
    fill=TEXT,
    weight=600,
    opacity=0.8,
)

# Adopt: host -> Git
chevron(1160, 440, 500, MINT)
c.text(520, 492, "Adopt", size=40, fill=MINT)
c.text(660, 492, "make Git match the host", size=30, fill=TEXT, weight=700)
c.text(
    520,
    534,
    "a reviewed pull request, never a silent commit",
    size=22,
    fill=TEXT,
    weight=600,
    opacity=0.8,
)
c.save("enforce-vs-adopt")
