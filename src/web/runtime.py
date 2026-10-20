"""Typed access to the engine from FastAPI routes.

Routes never reach into ``app.state`` with string keys; they call
``get_runtime(request)`` and get a ``WardenRuntime`` whose attributes are
fully typed.
"""

from dataclasses import dataclass

from fastapi import Request

from warden.config import WardenSettings
from warden.engine.reconciler import Reconciler
from warden.engine.state_projector import StateProjector
from web.db.repository import Store
from web.db.session import Database


@dataclass(slots=True)
class WardenRuntime:
    """Everything a route needs, wired once in the lifespan.

    Attributes:
        settings: Validated application settings.
        database: SQLite engine owner.
        store: Repository facade.
        projector: Snapshot holder and SSE broadcaster.
        reconciler: The engine.
    """

    settings: WardenSettings
    database: Database
    store: Store
    projector: StateProjector
    reconciler: Reconciler


def get_runtime(request: Request) -> WardenRuntime:
    """Fetch the runtime wired by the lifespan.

    Args:
        request: Incoming request.

    Returns:
        The process-wide runtime.

    Raises:
        RuntimeError: If the lifespan did not run (misconfigured server).
    """
    runtime = getattr(request.app.state, "warden", None)
    if not isinstance(runtime, WardenRuntime):
        raise RuntimeError("DriftWarden runtime is not initialised")
    return runtime
