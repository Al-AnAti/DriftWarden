# ruff: noqa: E501
"""Four steps from a manual change on the host to a host back in sync (1920x1080)."""

from draw import LINE, TEXT, Canvas, color_of, est_width

W, H = 1920, 1080
c = Canvas(
    W,
    H,
    "DriftWarden in four steps: a manual change on the host, the event captured, the operator decides, the host is back in sync",
)
c.title("From a manual change to a host back in sync")

xs = [300, 740, 1180, 1620]
accents = ["orange", "cyan", "violet", "mint"]
titles = ["Manual change", "Event captured", "Operator decides", "Back in sync"]
details = [
    (
        "docker run with no labels,",
        "an image or port edited by hand,",
        "a service removed from the host",
    ),
    (
        "the Docker socket emits the event,",
        "a 2 s quiet window, then every",
        "container is classified against Git",
    ),
    (
        "the dashboard repaints live over SSE;",
        "Enforce fixes the host, Adopt opens",
        "a pull request, Whitelist exempts it",
    ),
    (
        "the PR merges and the baseline is",
        "re-fetched, or Enforce lands: every",
        "row MATCHED, one audit row per action",
    ),
]
LY = 420
c.path(f"M{xs[0]},{LY} L{xs[-1]},{LY}", stroke=LINE, sw=6, head=False, glow=False)
for i, x in enumerate(xs):
    col = color_of(accents[i])
    c.circle(x, LY, 58, "#0f172a", stroke=col, sw=6, glow=c.glow(col, 16))
    c.text(x, LY + 18, str(i + 1), size=50, fill=col, anchor="middle")
    c.text(x, 565, titles[i], size=38, fill=col, anchor="middle")
    c.lines(x, 618, details[i], size=24, fill=TEXT, anchor="middle", lh=36, weight=600)


def row(cx, y, items, size=22):
    ws = [est_width(s, size) + size * 1.4 for s, _ in items]
    x = cx - (sum(ws) + 12 * (len(ws) - 1)) / 2
    for (s, acc), w in zip(items, ws):
        c.pill(x + w / 2, y, s, acc, size=size)
        x += w + 12


PY = 800
row(xs[1], PY, [("ROGUE", "rose"), ("DRIFTED", "amber"), ("MISSING", "cyan")])
row(xs[2], PY, [("Enforce", "mint"), ("Adopt", "violet"), ("Whitelist", "slate")])
row(xs[3], PY, [("MATCHED", "mint"), ("IN SYNC", "mint")])
c.save("timeline")
