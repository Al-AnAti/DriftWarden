# ruff: noqa: E501
"""End-to-end architecture workflow (refresh of images/workflow.jpg)."""

from draw import BORDER, CARD, CARD2, DIM, INDIGO, MUTED, TEXT, TONES, Canvas

W, H = 1700, 940
c = Canvas(
    W,
    H,
    "DriftWarden end-to-end workflow: inputs, engine, outputs, operator decisions and feedback loops",
)
c.header(
    "end-to-end workflow",
    "One FastAPI process: the engine runs in the lifespan, the control plane reads its snapshot",
)
ROSE, EM, AM, SKY, VIO, OR, SL, TE = (
    TONES[t][1] for t in ("rose", "emerald", "amber", "sky", "violet", "orange", "slate", "teal")
)


def col(x, s):
    c.text(x, 135, s, size=13, fill=DIM, weight=700, spacing=1.2)


col(60, "INPUTS")
col(400, "ENGINE  ·  src/warden")
col(1065, "STATE & LOGS")
col(1345, "OPERATOR  ·  src/web")

# --- inputs -----------------------------------------------------------
c.card(
    60,
    170,
    260,
    150,
    "GitHub repository",
    tone="indigo",
    lines=["TARGET_REPO / TARGET_BRANCH", "BASELINE_FILE_PATH", "", "docker-compose.yaml = truth"],
    mono_body=True,
    body_size=12,
)
c.card(
    60,
    640,
    260,
    150,
    "Docker host",
    tone="sky",
    lines=[
        "/var/run/docker.sock",
        "events · inspect · create",
        "start · stop · remove",
        "every container, running or not",
    ],
    mono_body=True,
    body_size=12,
)

# --- engine box -------------------------------------------------------
c.rect(380, 160, 640, 650, fill="rgba(15,23,42,0.6)", stroke=BORDER, dash="8 6", rx=18)
c.card(
    410,
    190,
    250,
    110,
    "baseline_parser",
    tone="teal",
    lines=["ruamel round-trip · canonical services", "name: beats COMPOSE_NAMESPACE"],
    body_size=11.5,
)
c.card(
    410,
    350,
    250,
    110,
    "pollers",
    tone="slate",
    lines=["baseline poll, ETag-cached", "PR poll on the same interval", "periodic full sweep"],
    body_size=11.5,
)
c.card(
    410,
    660,
    250,
    120,
    "docker_watcher",
    tone="sky",
    lines=[
        "event pump thread → asyncio.Queue",
        "debounce DRIFT_DEBOUNCE_SECONDS",
        "own-action cache ignores itself",
    ],
    body_size=11.5,
)

c.rect(710, 190, 280, 590, fill=CARD2, stroke=OR, rx=14, sw=1.8)
c.text(726, 220, "reconciler", size=15, fill=TEXT, weight=700)
c.text(726, 240, "one cycle per quiet window", size=11.5, fill=MUTED)
c.rect(726, 262, 248, 236, fill=CARD, stroke=AM, rx=10)
c.text(742, 288, "diff_engine", size=14, fill=TEXT, weight=700)
c.text(742, 306, "pure classification", size=11, fill=MUTED)
c.rect(742, 320, 216, 46, fill="rgba(49,46,129,0.5)", stroke=INDIGO, rx=8)
c.text(850, 339, "baseline service", size=12, fill=TEXT, anchor="middle", weight=600)
c.text(850, 356, "declared", size=10.5, fill=MUTED, anchor="middle")
c.text(850, 388, "vs", size=12, fill=DIM, anchor="middle", weight=700)
c.rect(742, 400, 216, 46, fill="rgba(12,74,110,0.5)", stroke=SKY, rx=8)
c.text(850, 419, "live container", size=12, fill=TEXT, anchor="middle", weight=600)
c.text(850, 436, "observed", size=10.5, fill=MUTED, anchor="middle")
c.text(742, 468, "image · ports · environment · volumes", size=10.5, fill=MUTED)
c.text(742, 484, "labels · restart · networks · runtime state", size=10.5, fill=MUTED)
c.lines(
    726,
    522,
    ["→ HostSnapshot: one DriftRecord per", "   service and per rogue container"],
    size=11.5,
    fill=MUTED,
)
y = 560
for s, t in (
    ("MATCHED", "emerald"),
    ("DRIFTED", "amber"),
    ("MISSING", "sky"),
    ("ROGUE", "rose"),
    ("PENDING_ADOPTION", "violet"),
    ("AWAITING_RESTART", "orange"),
):
    pass
c.pill(726, 562, "MATCHED", "emerald", size=10)
c.pill(816, 562, "DRIFTED", "amber", size=10)
c.pill(900, 562, "MISSING", "sky", size=10)
c.pill(726, 592, "ROGUE", "rose", size=10)
c.pill(800, 592, "PENDING_ADOPTION", "violet", size=10)
c.pill(726, 622, "AWAITING_RESTART", "orange", size=10)
c.rect(726, 665, 248, 100, fill=CARD, stroke=ROSE, rx=10, dash="5 4")
c.text(742, 690, "enforcer", size=14, fill=TEXT, weight=700)
c.lines(
    742,
    710,
    ["enforce mode only, after the", "grace window, 60 s cooldown", "per entry; never on startup"],
    size=11,
    fill=MUTED,
)

# --- state & logs ------------------------------------------------------
c.card(
    1065,
    190,
    240,
    110,
    "state_projector",
    tone="emerald",
    lines=["snapshot → every browser", "over Server-Sent Events"],
    body_size=11.5,
)
c.card(
    1065,
    350,
    240,
    150,
    "SQLite  warden.db",
    tone="slate",
    lines=[
        "audit_log",
        "drift_events",
        "pending_adoptions",
        "whitelisted_entities",
        "setting_overrides",
    ],
    mono_body=True,
    body_size=11.5,
)
c.card(
    1065,
    660,
    240,
    120,
    "Docker SDK actions",
    tone="rose",
    lines=["containers.create + start", "stop, then remove", "never docker compose"],
    mono_body=True,
    body_size=11.5,
)

# --- operator ---------------------------------------------------------
c.card(1345, 190, 300, 310, "Dashboard", tone="indigo", lines=[])
c.lines(
    1361,
    240,
    ["live rows, status pill, chimes", "/diff side-by-side · /buffer · /events · /config"],
    size=11.5,
    fill=MUTED,
)
c.pill(1361, 285, "Enforce", "rose", size=11)
c.text(1440, 300, "host matches Git", size=11.5, fill=MUTED)
c.pill(1361, 318, "Adopt", "emerald", size=11)
c.text(1432, 333, "Git matches the host, via a PR", size=11.5, fill=MUTED)
c.pill(1361, 351, "Whitelist", "slate", size=11)
c.text(1452, 366, "exempt a container or project", size=11.5, fill=MUTED)
c.pill(1361, 384, "Revoke Buffer", "violet", size=11)
c.text(1480, 399, "close the PR, evict entries", size=11.5, fill=MUTED)
c.pill(1361, 417, "Enforce All / Adopt All", "orange", size=11)
c.text(1361, 462, "scoped batch: one PR with a checklist body", size=11.5, fill=MUTED)
c.text(1361, 482, "observe mode: nothing happens without a click", size=11, fill=DIM, italic=True)

c.card(
    1345,
    560,
    300,
    110,
    "yaml_mutator + github_client",
    tone="emerald",
    lines=[
        "service block → ${KEY} placeholders,",
        "no container_name, comments kept",
        "branch warden/adopt-<name>-<ts> → PR",
    ],
    body_size=11.5,
)
c.card(
    1345,
    700,
    300,
    100,
    "state buffer",
    tone="violet",
    lines=[
        "PENDING_ADOPTION, exempt from enforcement;",
        "row in pending_adoptions, followed by the",
        "PR poller until merged, closed or revoked",
    ],
    body_size=11.5,
)

# --- arrows -----------------------------------------------------------
c.arrow([(320, 245), (410, 245)], stroke=INDIGO)
c.label(365, 232, "PyGithub", size=10.5, fill=INDIGO)
c.arrow([(535, 300), (535, 350)], stroke=TE)
c.arrow([(660, 405), (710, 405)], stroke=SL)
c.label(685, 392, "reconcile", size=10, fill=SL)
c.arrow([(320, 715), (410, 715)], stroke=SKY)
c.label(365, 702, "events", size=10.5, fill=SKY)
c.arrow([(660, 715), (710, 715)], stroke=SKY)
c.arrow([(990, 245), (1065, 245)], stroke=EM)
c.arrow([(990, 420), (1065, 420)], stroke=SL)
c.arrow([(990, 715), (1065, 715)], stroke=ROSE)
c.arrow([(1305, 245), (1345, 245)], stroke=EM)
c.label(1325, 232, "SSE", size=10.5, fill=EM)
c.arrow([(1305, 420), (1345, 420)], stroke=SL, tail=True)
# operator feedback loops
c.path("M1495,500 L1495,560", stroke=EM, sw=2.2)
c.label(1545, 536, "Adopt", size=11, fill=EM)
c.arrow([(1495, 670), (1495, 700)], stroke=VIO)
# merged -> baseline re-fetch back to GitHub (bottom loop)
c.path("M1495,800 L1495,875 L30,875 L30,245 L58,245", stroke=INDIGO, sw=2.2, dash="6 5")
c.label(
    760,
    892,
    "PR merged → baseline re-fetched → entry cleared → MATCHED (or AWAITING_RESTART for an adopted rogue)",
    size=11.5,
    fill=INDIGO,
)
# enforce -> docker host
c.path(
    "M1345,290 L1335,290 Q1325,290 1325,300 L1325,620 Q1325,630 1315,630 L1185,630 L1185,660",
    stroke=ROSE,
    sw=2.2,
)
c.label(1230, 617, "Enforce", size=11, fill=ROSE)
c.path(
    "M1065,750 L1010,750 Q1000,750 1000,760 L1000,830 Q1000,840 990,840 L190,840 L190,792",
    stroke=ROSE,
    sw=2.2,
)
c.label(
    620,
    828,
    "recreate / remove on the host; the next Docker event closes the loop",
    size=11.5,
    fill=ROSE,
)
c.footer("docs/overview.md · section 2")
c.save("workflow")
