# ruff: noqa: E501
"""Zero-trust evaluation order."""

from draw import BORDER, CARD, DIM, MUTED, TEXT, TONES, Canvas

W, H = 1500, 960
c = Canvas(
    W, H, "DriftWarden zero-trust evaluation order: how every container on the host is classified"
)
c.header(
    "zero-trust evaluation order",
    "Every container on the host, running or not, goes through these checks in this order on every cycle",
)
ROSE, EM, AM, SKY, VIO, OR, SL, TE = (
    TONES[t][1] for t in ("rose", "emerald", "amber", "sky", "violet", "orange", "slate", "teal")
)

sx, sw_, sh = 70, 560, 96
ys = [150, 280, 410, 540, 670]
titles = [
    (
        "1 · Hard exclusions",
        [
            "DriftWarden's own container · names starting with EXCLUDED_NAME_PREFIXES",
            "(warden-, driftwarden, k8s_) · the label warden.ignore=true",
        ],
    ),
    (
        "2 · Whitelist",
        [
            "Container id, container name or whole Compose project present in",
            "whitelisted_entities (SQLite), added from the dashboard, revoked on /config",
        ],
    ),
    (
        "3 · Managed namespace?",
        [
            "com.docker.compose.project equals the baseline's name:",
            "(or COMPOSE_NAMESPACE when the file has no name:)",
        ],
    ),
    (
        "4 · Map to a declared service",
        [
            "com.docker.compose.service label → <project>-<service>-<n> name",
            "pattern → explicit container_name. Labels state intent, Git decides legitimacy.",
        ],
    ),
    (
        "5 · Diff field by field",
        [
            "image (repo:tag, no digest) · ports · environment · volumes · labels · restart ·",
            "networks, plus the runtime state. Declared services with no container at all are MISSING.",
        ],
    ),
]
for (t, lines), y in zip(titles, ys):
    c.rect(sx, y, sw_, sh, fill=CARD, stroke=BORDER, rx=12)
    c.text(sx + 18, y + 30, t, size=15, fill=TEXT, weight=700)
    c.lines(sx + 18, y + 54, lines, size=11.5, fill=MUTED, lh=17)
for y1, y2 in zip(ys, ys[1:]):
    c.arrow([(sx + sw_ / 2, y1 + sh + 3), (sx + sw_ / 2, y2 - 3)], stroke=SL, sw=2.2)
    c.label(sx + sw_ / 2 + 36, y1 + sh + 22, "otherwise", size=11, fill=DIM)


def result(y, pills, text, color=SL, dy=0):
    c.arrow([(sx + sw_ + 4, y + sh / 2), (sx + sw_ + 60, y + sh / 2)], stroke=color, sw=2.2)
    px = sx + sw_ + 76
    for s, tone in pills:
        px += c.pill(px, y + sh / 2 - 11 + dy, s, tone, size=11, h=24) + 8
    c.lines(sx + sw_ + 76, y + sh / 2 + 32 + dy, text, size=11.5, fill=MUTED, lh=16)


result(
    ys[0],
    [("NOT EVALUATED", "slate")],
    ["Never appears on the dashboard; the daemon never touches it."],
    dy=-10,
)
result(
    ys[1],
    [("EXEMPT", "slate")],
    ["Skipped while the exemption exists; Enforce All never reaches it."],
    dy=-10,
)
c.label(sx + sw_ + 32, ys[2] + sh / 2 - 10, "no", size=11, fill=ROSE)
result(
    ys[2],
    [("ROGUE", "rose")],
    [
        "Unlabelled container, another Compose project, or spoofed labels.",
        "Exception: a running unlabelled container whose sanitised name is a declared",
        "service with no labelled instance (an adopted rogue after its PR merged):",
    ],
    color=ROSE,
    dy=-26,
)
c.pill(sx + sw_ + 76, ys[2] + sh / 2 + 58, "AWAITING_RESTART", "orange", size=11, h=24)
c.label(sx + sw_ + 32, ys[3] + sh / 2 - 10, "no", size=11, fill=ROSE)
result(
    ys[3],
    [("ROGUE", "rose")],
    [
        "Labels claim a service that Git does not declare, or a duplicate replica",
        "of a declared one.",
    ],
    color=ROSE,
    dy=-10,
)
c.arrow([(sx + sw_ + 4, ys[4] + sh / 2), (sx + sw_ + 60, ys[4] + sh / 2)], stroke=EM, sw=2.2)
px = sx + sw_ + 76
for s, tone, note in (
    ("MATCHED", "emerald", "every field equal, container running"),
    ("DRIFTED", "amber", "a field differs, or paused / restarting / dead"),
    ("MISSING", "sky", "stopped or exited, or no container at all"),
):
    w = c.pill(px, ys[4] + 12, s, tone, size=11, h=24)
    c.text(px + w + 10, ys[4] + 29, note, size=11.5, fill=MUTED)
    ys[4] += 30
ys[4] -= 90

# overlay row
oy = 800
c.rect(sx, oy, 1360, 86, fill="rgba(76,29,149,0.18)", stroke=VIO, rx=12, dash="7 5")
c.text(sx + 18, oy + 30, "Overlay after classification", size=15, fill=TEXT, weight=700)
c.pill(sx + 18, oy + 44, "PENDING_ADOPTION", "violet", size=11, h=24)
c.text(
    sx + 190,
    oy + 61,
    "An entry that is in the buffer (its Adopt PR is open) keeps this status instead of ROGUE / DRIFTED / MISSING and is exempt from enforcement.",
    size=12,
    fill=MUTED,
)
c.footer("src/warden/engine/diff_engine.py · tests/test_diff_engine.py")
c.save("evaluation-order")
