"""Clear DriftWarden's drift event and audit log history.

Usage::

    python scripts/reset_logs.py

The database comes from ``DATABASE_URL`` (environment or ``.env``), defaulting
to ``sqlite:///./warden.db``. Both tables are truncated in one WAL-safe
transaction through the project's own SQLAlchemy engine, so a running
DriftWarden instance keeps working while history is wiped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402  (needs src on sys.path)

from web.db.repository import Store  # noqa: E402
from web.db.session import Database  # noqa: E402

DEFAULT_DATABASE_URL = "sqlite:///./warden.db"


def _utf8_stdout() -> None:
    """Make sure the check mark prints on consoles that default to a legacy code page."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def resolve_database_url() -> str:
    """Pick the database URL the way the application does, without GitHub checks.

    Returns:
        ``DATABASE_URL`` from the environment or ``.env``, else the default.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = ROOT / ".env"
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("DATABASE_URL="):
                value = line.partition("=")[2].strip().strip("'\"")
                if value:
                    return value
    return DEFAULT_DATABASE_URL


def main(argv: list[str] | None = None) -> int:
    """Truncate the drift event and audit log tables.

    Args:
        argv: Optional ``[database_url]`` override, mainly for tests.

    Returns:
        Process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    url = args[0] if args else resolve_database_url()
    _utf8_stdout()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    database = Database(url)
    try:
        database.init()
        events, audit = Store(database, hostname="reset-logs").clear_logs()
    except Exception as exc:  # surface a clean one-line failure
        print(f"[x] Could not clear log history in {url}: {exc}", file=sys.stderr)
        return 1
    finally:
        database.close()
    print("[✓] Drift and audit log history cleared successfully.")
    print(f"    {events} drift events and {audit} audit rows removed from {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
