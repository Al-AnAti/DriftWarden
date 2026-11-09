# ruff: noqa: E501
"""Zero-trust evaluation order: how every container on the host is classified."""

from draw import LINE, MINT, ORANGE, SLATE, Canvas

W, H = 1500, 1000
c = Canvas(
    W, H, "DriftWarden zero-trust evaluation order: how every container on the host is classified"
)
c.title("Zero-trust evaluation order")

SX, SW = 420, 620
ys = [190, 330, 470, 610, 750]
steps = [
    "1   Hard exclusions",
    "2   Whitelist",
    "3   In the Compose namespace?",
    "4   Maps to a declared service?",
    "5   Field-by-field diff",
]
for y, s in zip(ys, steps):
    c.node(SX, y, SW, 86, s, "cyan", size=28)
for y1, y2 in zip(ys, ys[1:]):
    c.arrow([(SX, y1 + 51), (SX, y2 - 51)], stroke=LINE, sw=4, glow=False)


def result(y, color, word=None):
    c.arrow([(SX + SW / 2 + 8, y), (SX + SW / 2 + 110, y)], stroke=color, sw=4)
    if word:
        c.label(SX + SW / 2 + 58, y - 16, word, color, size=20)


PX = SX + SW / 2 + 130
result(ys[0], SLATE)
c.pill(PX, ys[0], "SKIPPED", "slate", size=24, anchor="start")
result(ys[1], SLATE)
c.pill(PX, ys[1], "EXEMPT", "slate", size=24, anchor="start")
result(ys[2], ORANGE, "no")
c.pill(PX, ys[2], "ROGUE", "orange", size=24, anchor="start")
result(ys[3], ORANGE, "no")
c.pill(PX, ys[3], "ROGUE", "orange", size=24, anchor="start")
result(ys[4], MINT)
x = PX
for s, acc in (("MATCHED", "mint"), ("DRIFTED", "orange"), ("MISSING", "orange")):
    x += c.pill(x, ys[4], s, acc, size=24, anchor="start") + 16

# overlay after classification
c.node(
    750,
    905,
    1300,
    100,
    "PENDING_ADOPTION",
    "cyan",
    sub="an entry with an open Adopt pull request keeps this status and is exempt from Enforce",
    size=28,
    mono=True,
    dash="12 10",
)
c.save("evaluation-order")
