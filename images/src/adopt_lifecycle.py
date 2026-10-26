# ruff: noqa: E501
"""Adopt pull-request lifecycle (replaces images/state_buffer.jpg)."""

from draw import CARD, INDIGO, MUTED, TEXT, TONES, Canvas

W, H = 1600, 900
c = Canvas(
    W,
    H,
    "DriftWarden Adopt lifecycle: from a detected drift to a merged, closed or revoked pull request",
)
c.header(
    "Adopt lifecycle",
    "Reverse-GitOps: make Git match the host through a reviewed pull request, never silently",
)
ROSE, EM, AM, SKY, VIO, OR, SL = (
    TONES[t][1] for t in ("rose", "emerald", "amber", "sky", "violet", "orange", "slate")
)

steps = [
    (
        "1",
        "Drift detected",
        "slate",
        [
            "A rogue container, a drifted service",
            "or a missing service shows up on the",
            "dashboard; the diff page shows both",
            "YAML panes side by side.",
        ],
    ),
    (
        "2",
        "Operator clicks Adopt",
        "emerald",
        [
            "A dark modal previews the mutation.",
            "Single entry: one PR per container,",
            "an open one is reused. Adopt All:",
            "every add / sync / remove in one PR.",
        ],
    ),
    (
        "3",
        "YAML synthesised",
        "teal",
        [
            "ruamel round-trip keeps comments.",
            "ROGUE: new service block.",
            "DRIFTED: only the changed fields.",
            "MISSING: the block is removed.",
        ],
    ),
    (
        "4",
        "Branch + pull request",
        "indigo",
        [
            "warden/adopt-<name>-<ts>",
            "(batch: warden/batch-adopt-<ts>).",
            "Env values never leave the host:",
            "keys become ${KEY}; no container_name.",
        ],
    ),
]
x0, cw, gap, y0, ch = 60, 345, 33, 140, 200
for i, (n, title, tone, lines) in enumerate(steps):
    x = x0 + i * (cw + gap)
    fill, stroke, color = TONES[tone]
    c.rect(x, y0, cw, ch, fill=CARD, stroke=stroke, rx=14, sw=1.8)
    c.circle(x + 30, y0 + 32, 15, fill, stroke=stroke, sw=1.5)
    c.text(x + 30, y0 + 37, n, size=14, fill=color, weight=800, anchor="middle")
    c.text(x + 56, y0 + 38, title, size=16, fill=TEXT, weight=700)
    c.lines(x + 18, y0 + 76, lines, size=12.5, fill=MUTED, lh=22)
    if i < len(steps) - 1:
        c.arrow([(x + cw + 4, y0 + ch / 2), (x + cw + gap - 4, y0 + ch / 2)], stroke=SL, sw=2.5)
for i, (code, px) in enumerate((("ROGUE", 86), ("DRIFTED", 162), ("MISSING", 245))):
    c.pill(x0 + px - 8, y0 + ch - 34, code, ("rose", "amber", "sky")[i], size=9.5, h=20)

# --- the buffer ---------------------------------------------------------
bx, by, bw, bh = 60, 400, 1480, 130
fill, stroke, color = TONES["violet"]
c.rect(bx, by, bw, bh, fill=fill, stroke=stroke, rx=16, sw=2)
c.text(bx + 24, by + 36, "PENDING_ADOPTION", size=17, fill=color, weight=800, mono=True)
c.text(bx + 24, by + 60, "the state buffer", size=13, fill=TEXT, weight=600)
c.lines(
    bx + 24,
    by + 86,
    [
        "Still visible, linked to its PR, exempt from enforcement for as long as the PR is open. Stored in pending_adoptions (SQLite), shown on /buffer with the PR state.",
        "The PR poller asks GitHub once per distinct PR on every baseline-poll interval. A new baseline that already satisfies an entry clears it too.",
    ],
    size=12.5,
    fill=TEXT,
    lh=20,
)
c.arrow(
    [(x0 + 3 * (cw + gap) + cw / 2, y0 + ch + 4), (x0 + 3 * (cw + gap) + cw / 2, by - 4)],
    stroke=INDIGO,
    sw=2.5,
)
c.label(x0 + 3 * (cw + gap) + cw / 2 + 60, by - 30, "PR opened", size=12, fill=INDIGO)

# --- outcomes -----------------------------------------------------------
outs = [
    (
        "PR merged",
        "emerald",
        [
            "Baseline re-fetched, entry cleared.",
            "DRIFTED service or deletion:",
            "→ MATCHED right away.",
            "Adopted ROGUE: → AWAITING_RESTART",
            "until docker compose up or Enforce",
            "recreates it with Compose labels.",
        ],
    ),
    (
        "PR closed unmerged",
        "amber",
        [
            "Entry cleared on the next poll.",
            "The workload returns to the state",
            "it came from: ROGUE, DRIFTED",
            "or MISSING, and is enforceable",
            "again.",
        ],
    ),
    (
        "Revoke Buffer (operator)",
        "rose",
        [
            "DriftWarden comments on the PR",
            "and closes it. Every entry sharing",
            "that PR is evicted (batch PRs",
            "cascade), one audit row each,",
            "and a reconcile runs at once.",
        ],
    ),
    (
        "PR state unknown",
        "slate",
        [
            "No PR, or GitHub unreachable:",
            "ADOPTION_BUFFER_TTL_SECONDS",
            "(default 600) is the only safety",
            "timer. Entries with a known PR",
            "state never expire.",
        ],
    ),
]
oy, oh = 630, 200
for i, (title, tone, lines) in enumerate(outs):
    x = x0 + i * (cw + gap)
    fill, stroke, color = TONES[tone]
    c.rect(x, oy, cw, oh, fill=CARD, stroke=stroke, rx=14, sw=1.8)
    c.text(x + 18, oy + 34, title, size=15, fill=color, weight=700)
    c.lines(x + 18, oy + 66, lines, size=12.5, fill=MUTED, lh=21)
    c.arrow([(x + cw / 2, by + bh + 4), (x + cw / 2, oy - 4)], stroke=stroke, sw=2.5)
c.footer("src/warden/engine/reconciler.py · adopt(), revoke(), the PR poller")
c.save("adopt-lifecycle")
