# ruff: noqa: E501
"""Four steps from a manual change on the host to a host back in sync."""

from draw import LINE, MUTED, Canvas, color_of

W, H = 1600, 580
c = Canvas(
    W,
    H,
    "DriftWarden in four steps: a manual change on the host, the event captured, the operator decides, the host is back in sync",
)
c.title("From a manual change to a host back in sync")

xs = [260, 620, 980, 1340]
accents = ["orange", "cyan", "cyan", "mint"]
titles = ["Manual change", "Event captured", "Operator decides", "Back in sync"]
subs = [
    ("docker run, a hot edit,", "a removed service"),
    ("Docker socket event,", "classified in seconds"),
    ("Enforce, Adopt or Whitelist:", "nothing without a click"),
    ("every row MATCHED,", "the pill says IN SYNC"),
]
LY = 230
c.path(f"M{xs[0]},{LY} L{xs[-1]},{LY}", stroke=LINE, sw=6, head=False, glow=False)
for i, x in enumerate(xs):
    col = color_of(accents[i])
    c.circle(x, LY, 46, "#0f172a", stroke=col, sw=5, glow=c.glow(col, 14))
    c.text(x, LY + 15, str(i + 1), size=42, fill=col, anchor="middle")
    c.text(x, 340, titles[i], size=32, fill=col, anchor="middle")
    c.lines(x, 382, subs[i], size=22, fill=MUTED, anchor="middle", lh=30)


def row(cx, y, items):
    ws = [len(s) * 22 * 0.6 + 22 * 1.4 for s, _ in items]
    x = cx - (sum(ws) + 14 * (len(ws) - 1)) / 2
    for (s, acc), w in zip(items, ws):
        c.pill(x + w / 2, y, s, acc, size=22)
        x += w + 14


row(xs[1], 490, [("ROGUE", "orange"), ("DRIFTED", "orange"), ("MISSING", "orange")])
row(xs[2], 490, [("Enforce", "orange"), ("Adopt", "mint")])
row(xs[3], 490, [("MATCHED", "mint")])
c.save("timeline")
