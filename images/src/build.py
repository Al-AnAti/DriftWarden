"""Regenerate every diagram SVG under images/ from the generator scripts in this directory.

Run from the repository root::

    python images/src/build.py          # writes images/*.svg
    node images/src/render.js           # rasterises them to images/*.png (needs Playwright)

The generators are plain Python with no third-party dependency; ``render.js`` uses the
Playwright Chromium that ships with the Claude Code environment, but any SVG rasteriser
(Inkscape, rsvg-convert, a browser screenshot) produces the same PNGs.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATORS = (
    "logo",
    "timeline",
    "workflow",
    "state_machine",
    "adopt_lifecycle",
    "evaluation_order",
    "enforce_vs_adopt",
    "architecture",
)


def main() -> int:
    sys.path.insert(0, str(HERE))
    os.chdir(HERE.parent)  # draw.Canvas.save writes to ./<name>.svg relative to images/
    for name in GENERATORS:
        runpy.run_path(str(HERE / f"{name}.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
