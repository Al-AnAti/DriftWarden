# ruff: noqa: E501
"""DriftWarden logo: isometric container, orange drift loop, git node path. Vector redraw."""

from draw import Canvas

S = 640
c = Canvas(S, S, "DriftWarden logo", bg=None)
c.raw("""<defs>
<linearGradient id="top" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9ee4f8"/><stop offset="1" stop-color="#5ec8ea"/></linearGradient>
<linearGradient id="left" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2aa6c9"/><stop offset="1" stop-color="#1d7f9e"/></linearGradient>
<linearGradient id="right" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#176e86"/><stop offset="1" stop-color="#0f4f61"/></linearGradient>
<linearGradient id="loop" gradientUnits="userSpaceOnUse" x1="0" y1="100" x2="640" y2="500"><stop offset="0" stop-color="#fb923c"/><stop offset="1" stop-color="#ea580c"/></linearGradient>
<linearGradient id="nodes" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#1f6f7f"/><stop offset="1" stop-color="#5ec8ea"/></linearGradient>
</defs>""")

ux, uy = 0.866, 0.5
a, b, H = 250, 150, 165  # length (right axis), width (left axis), height
T0 = (272.0, 96.0)


def P(i, j, k=0.0):
    """Point i along the right axis, j along the left axis, k down."""
    return (T0[0] + i * ux - j * ux, T0[1] + i * uy + j * uy + k)


def poly(pts, fill, stroke="none", sw=0):
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    c.raw(
        f'<polygon points="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"/>'
    )


cx, cy = P(a / 2, b / 2, H / 2)  # box centre
# --- loop: a tilted ellipse; the back half goes behind the box -------------
rx, ry, tilt = 282, 122, -22


def half(sweep):
    return (
        f'<g transform="rotate({tilt} {cx:.1f} {cy:.1f})"><path d="M{cx - rx:.1f},{cy:.1f} '
        f'A{rx} {ry} 0 0 {sweep} {cx + rx:.1f},{cy:.1f}" fill="none" stroke="url(#loop)" '
        f'stroke-width="26" stroke-linecap="round"/></g>'
    )


c.raw(half(1))  # back half (upper arc)

# --- container ----------------------------------------------------------
poly([P(0, 0), P(a, 0), P(a, b), P(0, b)], "url(#top)")
poly([P(0, b), P(a, b), P(a, b, H), P(0, b, H)], "url(#left)")
poly([P(a, 0), P(a, b), P(a, b, H), P(a, 0, H)], "url(#right)")
# corrugation slats on the two visible sides
n = 7
for s in range(n):
    i0 = a * (s + 0.25) / n
    i1 = a * (s + 0.75) / n
    poly(
        [P(i0, b, 12), P(i1, b, 12), P(i1, b, H - 12), P(i0, b, H - 12)], "#6fd3ee", opacity := None
    ) if False else None
    d = " ".join(
        f"{x:.1f},{y:.1f}"
        for x, y in [P(i0, b, 14), P(i1, b, 14), P(i1, b, H - 14), P(i0, b, H - 14)]
    )
    c.raw(f'<polygon points="{d}" fill="#7fdcf3" opacity="0.85"/>')
m = 4
for s in range(m):
    j0 = b * (s + 0.25) / m
    j1 = b * (s + 0.75) / m
    d = " ".join(
        f"{x:.1f},{y:.1f}"
        for x, y in [P(a, j0, 14), P(a, j1, 14), P(a, j1, H - 14), P(a, j0, H - 14)]
    )
    c.raw(f'<polygon points="{d}" fill="#2aa6c9" opacity="0.9"/>')
# edges
for pts in (
    [P(0, 0), P(a, 0), P(a, b), P(0, b)],
    [P(0, b), P(a, b), P(a, b, H), P(0, b, H)],
    [P(a, 0), P(a, b), P(a, b, H), P(a, 0, H)],
):
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    c.raw(
        f'<polygon points="{d}" fill="none" stroke="#0b3f4f" stroke-width="3" stroke-linejoin="round"/>'
    )

c.raw(half(0))  # front half (lower arc)

# --- git node path, bottom right ----------------------------------------
c.raw(
    '<g stroke="url(#nodes)" stroke-width="16" stroke-linecap="round" fill="none">'
    '<path d="M300,590 L410,540 L520,560 L600,500"/>'
    '<path d="M410,540 L470,470 L600,440"/></g>'
)
for x, y, fill in [
    (300, 590, "#1f6f7f"),
    (410, 540, "#2aa6c9"),
    (470, 470, "#2aa6c9"),
    (520, 560, "#3fb8da"),
    (600, 500, "#7fdcf3"),
    (600, 440, "#9ee4f8"),
]:
    c.circle(x, y, 17, fill, stroke="#062b36", sw=4)
c.save("logo")
