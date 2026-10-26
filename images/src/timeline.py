# ruff: noqa: E501
"""Four-step journey from a manual change to a host back in sync (replaces images/timeline_sequence.jpg)."""

from draw import BORDER, CARD, MUTED, TEXT, TONES, Canvas, est_width

W, H = 1600, 640
c = Canvas(
    W,
    H,
    "DriftWarden in four steps: a manual change, the event captured, the operator decides, the host is back in sync",
)
c.header(
    "in four steps",
    "From a manual change on the host to a reviewed baseline, in seconds, with nothing destructive happening on its own",
)
ROSE, EM, AM, SKY, VIO, OR, SL, TE = (
    TONES[t][1] for t in ("rose", "emerald", "amber", "sky", "violet", "orange", "slate", "teal")
)

xs = [230, 610, 990, 1370]
tones = ["teal", "orange", "indigo", "emerald"]
titles = ["Manual change on the host", "Event captured", "Operator decides", "Back in sync"]
bodies = [
    [
        "docker run with no labels, a hot-edited image or port,",
        "a service removed by hand: the host now disagrees",
        "with docker-compose.yaml in Git.",
    ],
    [
        "The Docker socket emits the event; after a 2 s quiet",
        "window the reconciler classifies every container as",
        "ROGUE, DRIFTED or MISSING. Dashboard repaints over",
        "SSE, the status pill turns, a chime plays.",
    ],
    [
        "Enforce: the host is recreated or cleaned to match Git.",
        "Adopt: the container is reverse-engineered into YAML",
        "and a pull request opens; the entry waits in the buffer.",
        "Whitelist: exempt it. Nothing happens without a click.",
    ],
    [
        "The PR is merged and the baseline re-fetched, or the",
        "Enforce action lands: every row is MATCHED, the pill",
        "says IN SYNC, and the audit log in SQLite has one row",
        "for every detection and action.",
    ],
]
# connector line
c.path(f"M{xs[0]},200 L{xs[-1]},200", stroke=SL, sw=4, head=False, opacity=0.6)
for i, x in enumerate(xs):
    fill, stroke, color = TONES[tones[i]]
    # icon area
    gy = 130
    if i == 0:  # terminal glyph
        c.rect(x - 34, gy - 26, 68, 50, fill=fill, stroke=stroke, rx=8, sw=2)
        c.text(x - 22, gy + 8, ">_", size=22, fill=color, weight=800, mono=True)
    elif i == 1:  # bolt in a ring
        c.circle(x, gy, 30, fill, stroke=stroke, sw=2)
        c.raw(
            f'<polygon points="{x + 6},{gy - 20} {x - 10},{gy + 3} {x + 1},{gy + 3} {x - 6},{gy + 20} {x + 10},{gy - 3} {x - 1},{gy - 3}" fill="{color}"/>'
        )
    elif i == 2:  # PR glyph
        c.circle(x - 16, gy - 16, 7, "none", stroke=color, sw=3)
        c.circle(x - 16, gy + 18, 7, "none", stroke=color, sw=3)
        c.circle(x + 18, gy + 18, 7, "none", stroke=color, sw=3)
        c.path(f"M{x - 16},{gy - 9} L{x - 16},{gy + 11}", stroke=color, sw=3, head=False)
        c.path(
            f"M{x - 4},{gy - 16} L{x + 8},{gy - 16} Q{x + 18},{gy - 16} {x + 18},{gy - 6} L{x + 18},{gy + 11}",
            stroke=color,
            sw=3,
            head=False,
        )
    else:  # shield check
        c.raw(
            f'<path d="M{x},{gy - 30} L{x + 26},{gy - 20} L{x + 24},{gy + 6} Q{x + 20},{gy + 24} {x},{gy + 32} Q{x - 20},{gy + 24} {x - 24},{gy + 6} L{x - 26},{gy - 20} Z" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        )
        c.path(
            f"M{x - 12},{gy} L{x - 3},{gy + 10} L{x + 14},{gy - 10}", stroke=color, sw=4, head=False
        )
    # numbered node on the line
    c.circle(x, 200, 26, "#0b1120", stroke=stroke, sw=4)
    c.text(x, 208, str(i + 1), size=22, fill=color, weight=800, anchor="middle")
    # title + body
    c.text(x, 268, titles[i], size=20, fill=color, weight=800, anchor="middle")
    c.lines(x, 300, bodies[i], size=12.5, fill=MUTED, anchor="middle", lh=20)
# state pills under steps 2 and 4


def pills(cx, items):
    ws = [est_width(s, 10.5, False, 700) + 20 for s, _ in items]
    x = cx - (sum(ws) + 10 * (len(ws) - 1)) / 2
    for (s, tone), w in zip(items, ws):
        c.pill(x, 400, s, tone, size=10.5, h=22)
        x += w + 10


pills(xs[1], [("ROGUE", "rose"), ("DRIFTED", "amber"), ("MISSING", "sky")])
pills(xs[2], [("Enforce", "rose"), ("Adopt", "emerald"), ("Whitelist", "slate")])
pills(xs[3], [("MATCHED", "emerald"), ("IN SYNC", "emerald")])
c.rect(60, 470, 1480, 110, fill=CARD, stroke=BORDER, rx=12)
c.text(80, 500, "Try it", size=14, fill=TEXT, weight=700)
c.lines(
    80,
    526,
    [
        "make demo-up starts the fixture stack (web + cache, both MATCHED). make demo-drift runs an unlabelled memcached, deletes web and recreates cache with the wrong port and labels:",
        "one ROGUE, one MISSING, one DRIFTED, pill DRIFT DETECTED. Enforce or Adopt each row from the dashboard; after an Adopt PR merges, the memcached shows AWAITING RESTART until",
        "docker compose up (or Enforce) recreates it with labels. make demo-down tears everything down.",
    ],
    size=12,
    fill=MUTED,
    lh=18,
)
c.footer("docs/overview.md · section 6, scripts/demo.sh")
c.save("timeline")
