# ruff: noqa: E501
"""One-process architecture map."""

from draw import CYAN, LINE, MINT, MUTED, ORANGE, SLATE, Canvas

W, H = 1500, 700
c = Canvas(
    W,
    H,
    "DriftWarden architecture: one FastAPI process with the engine and the web control plane, talking to GitHub, the Docker socket, the browser and SQLite",
)
c.title("Architecture")

# the process
c.rect(330, 130, 840, 510, fill="none", stroke=LINE, rx=28, sw=3, dash="14 12")
c.text(360, 172, "one FastAPI process", size=22, fill=MUTED, weight=700, spacing=1)

c.node(530, 330, 340, 170, "Engine", "cyan", sub="src/warden · watch, diff, reconcile", size=40)
c.node(970, 330, 340, 170, "Web", "mint", sub="src/web · dashboard, API, SSE", size=40)
c.arrow([(708, 330), (792, 330)], stroke=CYAN, sw=4)
c.label(750, 300, "snapshot", "cyan", size=20)
c.node(
    750,
    560,
    760,
    90,
    "src/shared",
    "slate",
    sub="the data contract: DriftStatus, DriftRecord, HostSnapshot",
    size=26,
    mono=True,
)

# the outside world
c.node(150, 260, 240, 100, "GitHub", "cyan", sub="baseline · pull requests", size=28)
c.node(150, 400, 240, 100, "Docker socket", "orange", sub="events · actions", size=28)
c.node(1350, 330, 240, 100, "Browser", "mint", sub="live over SSE", size=28)
c.node(1350, 560, 240, 100, "SQLite", "slate", sub="warden.db", size=28)

c.arrow([(272, 260), (368, 290)], stroke=CYAN, sw=4, tail=True)
c.arrow([(272, 400), (368, 370)], stroke=ORANGE, sw=4, tail=True)
c.arrow([(1132, 330), (1228, 330)], stroke=MINT, sw=4, tail=True)
c.arrow([(1132, 560), (1228, 560)], stroke=SLATE, sw=4, tail=True)
c.save("architecture")
