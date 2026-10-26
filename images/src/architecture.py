# ruff: noqa: E501
"""One-process architecture map (replaces the ASCII block in README)."""

from draw import BORDER, CARD, CARD2, INDIGO, MUTED, TEXT, TONES, Canvas

W, H = 1500, 900
c = Canvas(
    W,
    H,
    "DriftWarden architecture: one FastAPI process with the engine in the lifespan, SQLite for what must survive a restart",
)
c.header(
    "architecture",
    "Everything runs in one FastAPI process; the web layer reads the engine's in-memory snapshot through a typed accessor",
)
ROSE, EM, AM, SKY, VIO, OR, SL, TE = (
    TONES[t][1] for t in ("rose", "emerald", "amber", "sky", "violet", "orange", "slate", "teal")
)

# process box
c.rect(300, 130, 900, 640, fill="rgba(15,23,42,0.6)", stroke=INDIGO, dash="10 7", rx=20, sw=1.8)
c.text(324, 162, "FastAPI process", size=16, fill=TEXT, weight=800)
c.text(
    460,
    162,
    "uvicorn web.app:app  ·  PYTHONPATH=src  ·  no IPC, no subprocess, no docker compose",
    size=12,
    fill=MUTED,
    mono=True,
)

# warden
c.rect(324, 190, 520, 420, fill=CARD2, stroke=OR, rx=14)
c.text(340, 218, "src/warden", size=15, fill=TEXT, weight=700, mono=True)
c.text(460, 218, "the engine, started and stopped by the lifespan", size=12, fill=MUTED)


def mod(x, y, w, h, title, lines, tone="slate"):
    c.card(x, y, w, h, title, tone=tone, lines=lines, title_size=13, body_size=11, pad=12)


mod(
    340,
    238,
    238,
    92,
    "config.py",
    ["WardenSettings: .env + environment,", "overridden live by setting_overrides"],
)
mod(
    594,
    238,
    234,
    92,
    "core/baseline_parser",
    ["Compose → canonical services", "(ruamel round-trip)"],
    "teal",
)
mod(
    340,
    344,
    238,
    108,
    "core/docker_watcher",
    [
        "event pump thread → asyncio.Queue,",
        "inspect, create + start, stop, remove,",
        "image pull, networks",
    ],
    "sky",
)
mod(
    594,
    344,
    234,
    108,
    "engine/diff_engine",
    [
        "pure classification of one container",
        "against the baseline; per-field diff;",
        "both YAML panes",
    ],
    "amber",
)
mod(
    340,
    466,
    238,
    128,
    "engine/reconciler",
    [
        "5 background tasks: event pump,",
        "consumer (debounce), baseline poll,",
        "periodic sweep, PR poll. Enforce,",
        "Adopt, buffer, whitelist, settings",
    ],
    "orange",
)
mod(
    594,
    466,
    234,
    128,
    "automation/",
    [
        "github_client: baseline fetch (ETag),",
        "branch + commit + PR, PR state",
        "yaml_mutator: add / update / remove",
        "a service block, comments kept",
    ],
    "emerald",
)

# web
c.rect(860, 190, 320, 420, fill=CARD2, stroke=INDIGO, rx=14)
c.text(876, 218, "src/web", size=15, fill=TEXT, weight=700, mono=True)
c.text(956, 218, "the control plane", size=12, fill=MUTED)
mod(
    876,
    238,
    288,
    92,
    "app.py + lifespan",
    ["builds settings, store, watcher, GitHub", "client, reconciler; typed snapshot accessor"],
    "indigo",
)
mod(
    876,
    344,
    288,
    108,
    "routes/",
    [
        "pages: / (dashboard), /diff/<ref>, /buffer,",
        "/events, /config · JSON API, actions,",
        "/api/actions/batch · SSE /events stream",
    ],
    "indigo",
)
mod(
    876,
    466,
    288,
    128,
    "store (SQLAlchemy 2.0)",
    [
        "audit_log · drift_events ·",
        "pending_adoptions · whitelisted_entities",
        "· setting_overrides",
        "single locked writer, WAL",
    ],
    "slate",
)

# engine -> state projector arrow
c.rect(324, 630, 856, 44, fill="rgba(6,78,59,0.35)", stroke=EM, rx=10)
c.text(340, 657, "engine/state_projector", size=13, fill=TEXT, weight=700, mono=True)
c.text(
    530,
    657,
    "HostSnapshot in memory → every open browser over Server-Sent Events; dashboard.js repaints rows, pill and chimes",
    size=11,
    fill=MUTED,
)

# shared
c.rect(324, 700, 856, 50, fill="rgba(30,41,59,0.6)", stroke=SL, rx=10)
c.text(340, 730, "src/shared", size=13, fill=TEXT, weight=700, mono=True)
c.text(
    450,
    730,
    "the data contract: DriftStatus and other enums, Pydantic boundary models (DriftRecord, HostSnapshot), the DriftWardenError hierarchy",
    size=11,
    fill=MUTED,
)

# externals
c.card(
    40,
    250,
    210,
    100,
    "GitHub",
    tone="indigo",
    lines=["PyGithub, token with repo", "scope; ETag-cached reads"],
    body_size=11.5,
)
c.card(
    40,
    440,
    210,
    100,
    "Docker socket",
    tone="sky",
    lines=["/var/run/docker.sock", "(root-equivalent access)"],
    body_size=11.5,
    mono_body=True,
)
c.card(
    1250,
    250,
    210,
    110,
    "Browser",
    tone="emerald",
    lines=["Tailwind CDN, one CSS,", "one JS file, no build;", "no authentication"],
    body_size=11.5,
)
c.card(
    1250,
    470,
    210,
    100,
    "warden.db",
    tone="slate",
    lines=["SQLite file; ./data when", "run as a container"],
    body_size=11.5,
)

c.arrow([(250, 300), (300, 300)], stroke=INDIGO, tail=True)
c.arrow([(250, 490), (300, 490)], stroke=SKY, tail=True)
c.arrow([(1200, 300), (1250, 300)], stroke=EM, tail=True)
c.text(1225, 328, "HTTP · SSE", size=10, fill=EM, anchor="middle")
c.arrow([(1200, 520), (1250, 520)], stroke=SL, tail=True)
c.text(1225, 548, "SQLAlchemy", size=10, fill=SL, anchor="middle")
c.text(275, 328, "baseline, PRs", size=10, fill=INDIGO, anchor="middle")
c.text(275, 518, "events, actions", size=10, fill=SKY, anchor="middle")

c.rect(40, 790, 1420, 70, fill=CARD, stroke=BORDER, rx=10)
c.text(60, 816, "Import rule", size=13, fill=TEXT, weight=700)
c.text(
    170,
    816,
    "shared ← warden ← web.  warden reaches the SQLite store only through a Protocol, so the engine stays testable without FastAPI; the tests run without Docker and without GitHub.",
    size=12,
    fill=MUTED,
)
c.text(60, 842, "Runtime split", size=13, fill=TEXT, weight=700)
c.text(
    170,
    842,
    "in memory: baseline + ETag, current snapshot, own-action cache, cooldowns.  In SQLite: audit, transitions, the buffer, the whitelist, setting overrides (.env is never written).",
    size=12,
    fill=MUTED,
)
c.footer("README.md · Architecture")
c.save("architecture")
