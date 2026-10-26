# ruff: noqa: E501
"""Enforce vs Adopt: the two directions."""

from draw import BORDER, CARD, DIM, MUTED, TEXT, TONES, Canvas

W, H = 1500, 760
c = Canvas(
    W,
    H,
    "DriftWarden two actions, two directions: Enforce makes the host match Git, Adopt makes Git match the host",
)
c.header(
    "two actions, two directions",
    "Exactly two verbs on every row, every modal and the diff page; what each does depends only on the entry's state",
)
ROSE, EM, AM, SKY, VIO, OR, SL = (
    TONES[t][1] for t in ("rose", "emerald", "amber", "sky", "violet", "orange", "slate")
)

# endpoints
c.card(60, 150, 300, 520, "Git", tone="indigo", lines=[])
c.lines(
    76,
    200,
    [
        "docker-compose.yaml on GitHub",
        "TARGET_REPO @ TARGET_BRANCH",
        "",
        "the declarative single source",
        "of truth; parsed into canonical",
        "services and cached in memory",
    ],
    size=12.5,
    fill=MUTED,
    lh=20,
)
c.text(76, 345, "Every change here is a reviewed commit:", size=12, fill=TEXT, weight=600)
c.lines(
    76,
    367,
    ["baseline poller (ETag) picks it up,", "re-arms the grace window and", "reconciles."],
    size=12.5,
    fill=MUTED,
    lh=20,
)
c.card(1140, 150, 300, 520, "Docker host", tone="sky", lines=[])
c.lines(
    1156,
    200,
    [
        "every container, running or not,",
        "seen through /var/run/docker.sock",
        "",
        "where drift happens: docker run,",
        "hot edits, deleted services,",
        "foreign Compose projects",
    ],
    size=12.5,
    fill=MUTED,
    lh=20,
)
c.text(1156, 345, "Every change here is an event:", size=12, fill=TEXT, weight=600)
c.lines(
    1156,
    367,
    [
        "debounced, classified against the",
        "baseline, pushed to the dashboard",
        "over SSE within seconds.",
    ],
    size=12.5,
    fill=MUTED,
    lh=20,
)


def big_arrow(x1, x2, y, color, h=46):
    """A wide chevron arrow from x1 to x2 (direction by sign)."""
    d = 1 if x2 > x1 else -1
    tip = 34 * d
    pts = [(x1, y - h / 2), (x2 - tip, y - h / 2), (x2, y), (x2 - tip, y + h / 2), (x1, y + h / 2)]
    c.raw(
        '<polygon points="'
        + " ".join(f"{x},{y}" for x, y in pts)
        + f'" fill="{color}" opacity="0.18" stroke="{color}" stroke-width="2"/>'
    )


# Enforce: Git -> host
big_arrow(400, 1100, 220, ROSE)
c.pill(540, 206, "Enforce", "rose", size=14, h=30)
c.text(650, 227, "make the host match Git", size=16, fill=TEXT, weight=700)
rows = [
    ("DRIFTED", "amber", "remove the live container, recreate it exactly as declared"),
    ("MISSING", "sky", "create the declared service that has no container"),
    ("AWAITING_RESTART", "orange", "recreate the adopted container with its Compose labels"),
    ("ROGUE", "rose", "Git declares nothing: graceful stop, then forced remove"),
]
y = 268
for s, tone, txt in rows:
    w = c.pill(430, y, s, tone, size=10.5, h=22)
    c.text(600, y + 15, txt, size=12.5, fill=MUTED)
    y += 30
c.text(
    430,
    y + 14,
    "Docker SDK only: containers.create + start, stop, remove. Never docker compose. Every action audited.",
    size=11.5,
    fill=DIM,
    italic=True,
)

# Adopt: host -> Git
big_arrow(1100, 400, 470, EM)
c.pill(540, 456, "Adopt", "emerald", size=14, h=30)
c.text(640, 477, "make Git match the host  (Reverse-GitOps)", size=16, fill=TEXT, weight=700)
rows = [
    ("ROGUE", "rose", "reverse-engineer the container into a new service block"),
    ("DRIFTED", "amber", "sync only the fields that changed on the existing service"),
    ("MISSING", "sky", "remove the service block, so Git stops declaring it"),
]
y = 518
for s, tone, txt in rows:
    w = c.pill(430, y, s, tone, size=10.5, h=22)
    c.text(600, y + 15, txt, size=12.5, fill=MUTED)
    y += 30
c.text(
    430,
    y + 14,
    "Comment-preserving YAML edit, ${KEY} placeholders instead of values, no container_name, one PR per",
    size=11.5,
    fill=DIM,
    italic=True,
)
c.text(
    430,
    y + 31,
    "container (or one batch PR). The entry waits in the buffer as PENDING_ADOPTION until the PR lands.",
    size=11.5,
    fill=DIM,
    italic=True,
)

# modes strip
c.rect(400, 690, 700, 50, fill=CARD, stroke=BORDER, rx=10)
c.pill(416, 704, "observe", "slate", size=11, h=22)
c.text(500, 719, "nothing happens without a click (default)", size=12, fill=MUTED)
c.pill(760, 704, "enforce", "rose", size=11, h=22)
c.text(842, 719, "Enforce runs by itself after the grace window", size=12, fill=MUTED)
c.footer("docs/overview.md · section 2, step 6")
c.save("enforce-vs-adopt")
