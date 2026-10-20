"""JSON API: live state, Server-Sent Events, operator actions and audit data."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from shared.models import (
    ActionResult,
    AdoptionRequest,
    AuditLogEntry,
    PendingAdoption,
    WhitelistEntry,
    WhitelistRequest,
)
from warden.engine.state_projector import snapshot_payload
from web.runtime import get_runtime

router = APIRouter(prefix="/api", tags=["Control Plane API"])

SSE_KEEPALIVE_SECONDS = 15.0


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Liveness and a summary of the engine's health."""
    runtime = get_runtime(request)
    snapshot = runtime.projector.snapshot
    return {
        "status": "healthy",
        "mode": runtime.settings.warden_mode,
        "enforcement_active": runtime.reconciler.enforcement_active,
        "docker_connected": snapshot.docker_connected,
        "baseline_available": snapshot.baseline.available,
        "baseline_source": snapshot.baseline.source.value,
        "overall": snapshot.overall,
        "version": runtime.projector.version,
    }


@router.get("/state")
def state(request: Request) -> dict[str, Any]:
    """The full reconciled snapshot."""
    runtime = get_runtime(request)
    return {
        "version": runtime.projector.version,
        "snapshot": snapshot_payload(runtime.projector.snapshot),
    }


@router.get("/state/stream")
async def state_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events: one ``snapshot`` event per reconcile cycle."""
    runtime = get_runtime(request)
    projector = runtime.projector
    queue = projector.subscribe()

    async def generator() -> AsyncIterator[str]:
        try:
            initial = json.dumps(
                {"version": projector.version, "snapshot": snapshot_payload(projector.snapshot)}
            )
            yield f"event: snapshot\ndata: {initial}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: snapshot\ndata: {message}\n\n"
        finally:
            projector.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/reconcile")
async def reconcile_now(request: Request) -> dict[str, Any]:
    """Force a reconcile cycle and a baseline refresh."""
    runtime = get_runtime(request)
    await runtime.reconciler.refresh_baseline(force=True)
    snapshot = await runtime.reconciler.reconcile("manual")
    return {"version": runtime.projector.version, "snapshot": snapshot_payload(snapshot)}


@router.get("/containers/{ref}")
def container_detail(request: Request, ref: str) -> dict[str, Any]:
    """One record with its structured diff."""
    runtime = get_runtime(request)
    record = runtime.projector.snapshot.find(ref)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No container or service named '{ref}'")
    return record.model_dump(mode="json")


@router.post("/actions/revert/{ref}", response_model=ActionResult)
async def revert(request: Request, ref: str) -> ActionResult:
    """Recreate a drifted or missing service from the Git baseline."""
    logger.info("Manual revert requested for {}", ref)
    return await get_runtime(request).reconciler.revert(ref)


@router.post("/actions/kill/{ref}", response_model=ActionResult)
async def kill(request: Request, ref: str) -> ActionResult:
    """Stop and remove a rogue container."""
    logger.info("Manual kill requested for {}", ref)
    return await get_runtime(request).reconciler.kill(ref)


@router.post("/actions/adopt/{ref}", response_model=ActionResult)
async def adopt(
    request: Request,
    ref: str,
    payload: AdoptionRequest | None = Body(default=None),
) -> ActionResult:
    """Open a Reverse-GitOps pull request for a rogue or drifted container."""
    note = payload.note if payload is not None else None
    logger.info("Adoption requested for {}", ref)
    return await get_runtime(request).reconciler.adopt(ref, note)


@router.get("/whitelist", response_model=list[WhitelistEntry])
def list_whitelist(request: Request) -> list[WhitelistEntry]:
    """All operator exemptions."""
    return get_runtime(request).store.list_whitelist()


@router.post("/whitelist", response_model=WhitelistEntry)
async def add_whitelist(request: Request, payload: WhitelistRequest) -> WhitelistEntry:
    """Exempt a container or Compose project from evaluation."""
    return await get_runtime(request).reconciler.whitelist(payload)


@router.delete("/whitelist/{entry_id}", response_model=ActionResult)
async def remove_whitelist(request: Request, entry_id: int) -> ActionResult:
    """Remove an exemption."""
    entry = await get_runtime(request).reconciler.unwhitelist(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Whitelist entry {entry_id} not found")
    return ActionResult(
        ok=True,
        action="unwhitelist",
        target=entry.target_identifier,
        message=(
            f"Removed {entry.target_type.value.lower()} exemption for {entry.target_identifier}."
        ),
    )


@router.get("/audit", response_model=list[AuditLogEntry])
def audit(request: Request, limit: int = 200) -> list[AuditLogEntry]:
    """Most recent audit rows."""
    return get_runtime(request).store.list_audit(limit=min(max(limit, 1), 1000))


@router.get("/events")
def drift_events(request: Request, limit: int = 200) -> list[dict[str, object]]:
    """Most recent status transitions."""
    return get_runtime(request).store.list_drift_events(limit=min(max(limit, 1), 1000))


@router.get("/buffer", response_model=list[PendingAdoption])
def buffer(request: Request) -> list[PendingAdoption]:
    """Active adoption buffer entries."""
    return get_runtime(request).reconciler.pending
