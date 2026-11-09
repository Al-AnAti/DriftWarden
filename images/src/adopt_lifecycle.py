# ruff: noqa: E501
"""Adopt lifecycle: from a detected drift to a merged or closed pull request (1920x1080)."""

from draw import LINE, MINT, ORANGE, VIOLET, Canvas

W, H = 1920, 1080
c = Canvas(
    W,
    H,
    "DriftWarden Adopt lifecycle: drift detected, Adopt clicked, YAML synthesised, pull request opened, the PENDING_ADOPTION buffer, and the two ways out",
)
c.title("Adopt lifecycle")

# the four steps
steps = [
    ("Drift detected", "orange", "ROGUE · DRIFTED · MISSING"),
    ("Adopt", "mint", "one click, preview first"),
    ("YAML synthesised", "cyan", "secrets become ${KEY}"),
    ("Pull request", "violet", "warden/adopt-<name>"),
]
xs = [300, 740, 1180, 1620]
Y1 = 290
for i, (label, acc, sub) in enumerate(steps):
    c.node(xs[i], Y1, 370, 130, label, acc, sub=sub, size=34)
    if i:
        c.arrow([(xs[i - 1] + 195, Y1), (xs[i] - 195, Y1)], stroke=LINE, sw=5, glow=False)

# the buffer
Y2 = 580
c.node(
    960,
    Y2,
    1690,
    130,
    "PENDING_ADOPTION",
    "violet",
    sub="in the buffer while the pull request is open · exempt from Enforce",
    size=34,
    mono=True,
)
c.arrow([(xs[3], Y1 + 75), (xs[3], Y2 - 75)], stroke=VIOLET, sw=5)

# the two ways out
Y3 = 870
outs = [
    (620, "PR merged", "mint", "→ MATCHED / AWAITING_RESTART", MINT),
    (1300, "PR closed", "orange", "→ back to the state it came from", ORANGE),
]
for x, label, acc, sub, col in outs:
    c.arrow([(x, Y2 + 75), (x, Y3 - 75)], stroke=col, sw=5)
    c.node(x, Y3, 600, 130, label, acc, sub=sub, size=34)
c.save("adopt-lifecycle")
