# ruff: noqa: E501
"""Two verbs, two modes: what Enforce and Adopt do per state, and when they run (1920x1080)."""

from draw import MUTED, TEXT, Canvas, color_of

W, H = 1920, 1080
c = Canvas(
    W,
    H,
    "DriftWarden two verbs, two modes: what Enforce and Adopt do for each drift state, and observe versus enforce mode",
)
c.title("Two verbs, two modes")

COLS = {
    "Enforce": (520, "rose", "make the host match Git"),
    "Adopt": (1400, "mint", "make Git match the host"),
}
CW = 760
rows = {
    "Enforce": [
        ("DRIFTED", "amber", "recreate the container exactly as declared"),
        ("MISSING", "cyan", "create the declared service"),
        ("ROGUE", "rose", "stop it, then remove it"),
        ("AWAITING_RESTART", "orange", "recreate it with its Compose labels"),
    ],
    "Adopt": [
        ("ROGUE", "rose", "add a service block to the compose file"),
        ("DRIFTED", "amber", "sync only the fields that changed"),
        ("MISSING", "cyan", "remove the service block"),
        ("PENDING_ADOPTION", "violet", "parked here until the pull request lands"),
    ],
}
for verb, (cx, acc, sub) in COLS.items():
    c.node(cx, 250, CW, 120, verb, acc, sub=sub, size=40)
    y = 410
    for state, sacc, action in rows[verb]:
        w = c.pill(cx - CW / 2 + 10, y, state, sacc, size=22, anchor="start", mono=True)
        c.text(cx - CW / 2 + 10 + 270, y + 8, action, size=24, fill=TEXT, weight=600)
        y += 80
    c.text(
        cx - CW / 2 + 10,
        y + 10,
        "Docker SDK only, never docker compose"
        if verb == "Enforce"
        else "one reviewed pull request per Adopt",
        size=20,
        fill=MUTED,
        weight=600,
    )

# modes
c.node(
    520,
    865,
    CW,
    130,
    "observe mode  (default)",
    "slate",
    sub="nothing happens on the host without a click",
    size=32,
)
c.node(
    1400,
    865,
    CW,
    130,
    "enforce mode",
    "rose",
    sub="Enforce runs by itself after the grace window",
    size=32,
)
c.text(
    960,
    990,
    "the buffer, the whitelist and excluded containers are never touched in either mode",
    size=20,
    fill=color_of("slate"),
    weight=600,
    anchor="middle",
)
c.save("6-two-verbs")
