# ruff: noqa: E501
"""End-to-end workflow: inputs, engine, dashboard and the two feedback loops."""

from draw import CYAN, MINT, ORANGE, Canvas

W, H = 1600, 800
c = Canvas(
    W,
    H,
    "DriftWarden end-to-end workflow: Git and the Docker host feed the engine, the dashboard shows the result, Enforce and Adopt close the loop",
)
c.title("End-to-end workflow")

# inputs
c.node(260, 300, 320, 120, "Git", "cyan", sub="docker-compose.yaml is the truth", size=34)
c.node(260, 580, 320, 120, "Docker host", "orange", sub="every container, running or not", size=34)
# engine
c.node(800, 440, 430, 200, "Engine", "cyan", sub="watch · diff · classify", size=40)
c.text(
    800,
    512,
    "MATCHED · DRIFTED · MISSING · ROGUE",
    size=20,
    fill=CYAN,
    weight=700,
    anchor="middle",
    opacity=0.9,
)
# operator
c.node(1350, 440, 320, 200, "Dashboard", "mint", sub="the operator decides", size=40)
c.pill(1292, 512, "Enforce", "orange", size=20)
c.pill(1418, 512, "Adopt", "mint", size=20)

# primary flows
c.arrow([(420, 300), (585, 385)], stroke=CYAN, sw=5)
c.label(505, 320, "baseline", "cyan")
c.arrow([(420, 580), (585, 495)], stroke=ORANGE, sw=5)
c.label(505, 575, "events", "orange")
c.arrow([(1015, 440), (1190, 440)], stroke=MINT, sw=5)
c.label(1102, 418, "snapshot", "mint")

# feedback loops
c.path("M1350,340 L1350,170 L260,170 L260,232", stroke=MINT, sw=4)
c.label(800, 178, "Adopt: a pull request makes Git match the host", "mint")
c.path("M1350,540 L1350,710 L260,710 L260,648", stroke=ORANGE, sw=4)
c.label(800, 718, "Enforce: the host is recreated to match Git", "orange")
c.save("workflow")
