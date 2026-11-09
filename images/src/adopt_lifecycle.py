# ruff: noqa: E501
"""Adopt lifecycle: from a detected drift to a merged, closed or revoked pull request."""

from draw import CYAN, LINE, MINT, ORANGE, Canvas

W, H = 1600, 760
c = Canvas(
    W,
    H,
    "DriftWarden Adopt lifecycle: drift detected, Adopt clicked, YAML synthesised, pull request opened, the PENDING_ADOPTION buffer, and the three ways out",
)
c.title("Adopt lifecycle")

steps = [
    ("Drift detected", "orange", "ROGUE · DRIFTED · MISSING"),
    ("Adopt", "mint", "one click, preview first"),
    ("YAML synthesised", "cyan", "secrets become ${KEY}"),
    ("Pull request", "cyan", "warden/adopt-<name>"),
]
xs = [260, 620, 980, 1340]
Y1 = 220
for i, (label, acc, sub) in enumerate(steps):
    c.node(xs[i], Y1, 320, 110, label, acc, sub=sub, size=30)
    if i:
        c.arrow([(xs[i - 1] + 168, Y1), (xs[i] - 168, Y1)], stroke=LINE, sw=4, glow=False)

# the buffer
Y2 = 430
c.node(
    800,
    Y2,
    1390,
    110,
    "PENDING_ADOPTION",
    "cyan",
    sub="in the buffer while the pull request is open · exempt from Enforce",
    size=30,
    mono=True,
)
c.arrow([(xs[3], Y1 + 63), (xs[3], Y2 - 63)], stroke=CYAN, sw=4)

# outcomes
Y3 = 640
outs = [
    (300, "PR merged", "mint", "→ MATCHED  (a ROGUE → AWAITING_RESTART)", MINT),
    (800, "PR closed", "orange", "→ back to the state it came from", ORANGE),
    (1300, "Revoke Buffer", "orange", "DriftWarden closes the PR → back", ORANGE),
]
for x, label, acc, sub, col in outs:
    c.arrow([(x, Y2 + 63), (x, Y3 - 63)], stroke=col, sw=4)
    c.node(x, Y3, 460, 110, label, acc, sub=sub, size=30)
c.save("adopt-lifecycle")
