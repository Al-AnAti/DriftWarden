"""The reconciliation engine: baseline polling, event-driven cycles, actions.

One ``Reconciler`` lives inside the FastAPI lifespan. It owns the Docker
watcher, the cached baseline, the adoption buffer, the whitelist index and
the enforcement state machine. Routes call its ``revert``/``kill``/``adopt``/
``whitelist`` coroutines; everything else runs as background tasks on the
application's event loop.
"""

import asyncio
import hashlib
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from shared.constants import (
    WARDEN_ACTION_LABEL,
    AuditAction,
    BaselineSource,
    DriftStatus,
    WhitelistTargetType,
)
from shared.exceptions import (
    AdoptionError,
    BaselineParseError,
    ConfigurationError,
    ContainerRecreateError,
    DockerConnectionError,
    DriftWardenError,
    GitBaselineFetchError,
    RogueContainerKillError,
    YAMLMutationError,
)
from shared.models import (
    ActionResult,
    BaselineInfo,
    DriftRecord,
    HostSnapshot,
    PendingAdoption,
    RawDockerEvent,
    WhitelistEntry,
    WhitelistRequest,
)
from warden.automation.github_client import AdoptionPullRequest, GitHubClient
from warden.automation.yaml_mutator import (
    AdoptionSpec,
    add_service,
    sanitise_service_name,
    update_service,
)
from warden.config import WardenSettings
from warden.core.baseline_parser import Baseline, parse_baseline
from warden.core.docker_watcher import DockerWatcher, LiveContainer
from warden.engine.diff_engine import (
    EvaluationContext,
    WhitelistIndex,
    evaluate,
    observed_service,
    project_networks,
)
from warden.engine.state_projector import StateProjector
from warden.engine.store import StateStore

ACTION_CACHE_TTL_SECONDS = 20.0
"""How long a container id/name/action id stays in the self-action cache."""

ENFORCEMENT_COOLDOWN_SECONDS = 60.0
"""Minimum time between two autonomous actions on the same entry (loop guard)."""


class Reconciler:
    """Coordinates baseline ingestion, Docker events, diffing and remediation.

    Args:
        settings: Validated application settings.
        watcher: Docker access layer.
        store: Durable state (audit, buffer, whitelist, events).
        projector: Snapshot holder and SSE broadcaster.
        github: GitHub client, or ``None`` when the baseline is mocked.
    """

    def __init__(
        self,
        settings: WardenSettings,
        watcher: DockerWatcher,
        store: StateStore,
        projector: StateProjector,
        github: GitHubClient | None,
    ) -> None:
        self.settings = settings
        self.watcher = watcher
        self.store = store
        self.projector = projector
        self.github = github

        self.baseline: Baseline | None = None
        self.baseline_error: str | None = None
        self._etag: str | None = None
        self._baseline_changed_at: float | None = None
        self._mock_digest: str | None = None

        self._queue: asyncio.Queue[RawDockerEvent] = asyncio.Queue()
        self._stop = threading.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._cycle_lock = asyncio.Lock()
        self._action_cache: dict[str, float] = {}
        self._enforced_at: dict[str, float] = {}
        self._image_env_cache: dict[str, dict[str, str]] = {}
        self._image_labels_cache: dict[str, dict[str, str]] = {}
        self._pending: dict[str, PendingAdoption] = {}
        self._whitelist = WhitelistIndex()
        self.startup_done = False
        self.last_cycle_error: str | None = None
        self.docker_connected = False
        self.docker_error: str | None = None

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Load durable state, fetch the baseline, run the observe-only pass, start loops."""
        await asyncio.to_thread(self._reload_durable_state)
        await self.refresh_baseline(force=True)
        await self.reconcile("startup")
        self.startup_done = True
        await asyncio.to_thread(
            self.store.record_audit,
            AuditAction.ENGINE_STARTED,
            self.settings.hostname,
            f"mode={self.settings.warden_mode} baseline={self.baseline_info().source.value}",
        )
        self._tasks = [
            asyncio.create_task(
                self.watcher.pump_events(self._queue, self._stop), name="docker-events"
            ),
            asyncio.create_task(self._event_loop(), name="event-consumer"),
            asyncio.create_task(self._baseline_loop(), name="baseline-poller"),
            asyncio.create_task(self._sweep_loop(), name="sweep"),
        ]
        logger.info("Reconciler started in {} mode", self.settings.warden_mode)

    async def stop(self) -> None:
        """Stop background loops and close the Docker client."""
        self._stop.set()
        self.watcher.close()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        logger.info("Reconciler stopped")

    def _reload_durable_state(self) -> None:
        """Rebuild the whitelist index and adoption buffer from SQLite."""
        index = WhitelistIndex()
        for entry in self.store.list_whitelist():
            if entry.target_type is WhitelistTargetType.PROJECT:
                index.projects.add(entry.target_identifier)
            else:
                index.containers.add(entry.target_identifier)
        self._whitelist = index
        self._pending = {p.container_id: p for p in self.store.active_pending()}

    # ------------------------------------------------------------- baseline

    @property
    def enforcement_active(self) -> bool:
        """Whether this cycle may take destructive action."""
        if not self.settings.enforce or not self.startup_done or self.baseline is None:
            return False
        if self._baseline_changed_at is None:
            return True
        return (
            time.monotonic() - self._baseline_changed_at
        ) >= self.settings.enforcement_grace_seconds

    def baseline_info(self) -> BaselineInfo:
        """Describe the cached baseline for the web layer."""
        if self.baseline is None:
            return BaselineInfo(
                source=BaselineSource.UNAVAILABLE,
                available=False,
                repo=self.settings.target_repo,
                branch=self.settings.target_branch,
                path=self.settings.baseline_file_path,
                error=self.baseline_error,
            )
        return BaselineInfo(
            source=self.baseline.source,
            available=True,
            project_name=self.baseline.project_name,
            repo=self.settings.target_repo
            if self.baseline.source is BaselineSource.GITHUB
            else None,
            branch=self.settings.target_branch
            if self.baseline.source is BaselineSource.GITHUB
            else None,
            path=(
                self.settings.baseline_file_path
                if self.baseline.source is BaselineSource.GITHUB
                else self.settings.mock_baseline_path
            ),
            sha=self.baseline.sha,
            fetched_at=self.baseline.fetched_at,
            services=self.baseline.service_names,
            error=self.baseline_error,
            raw_yaml=self.baseline.raw_yaml,
        )

    async def refresh_baseline(self, force: bool = False) -> bool:
        """Poll the baseline source and replace the cached baseline if it changed.

        Fetch failures keep the last known baseline and set ``baseline_error``.

        Args:
            force: Ignore the ETag cache and re-read the content.

        Returns:
            ``True`` if a new baseline was installed.
        """
        try:
            if self.settings.allow_mock_baseline:
                changed = await asyncio.to_thread(self._load_mock_baseline, force)
            else:
                changed = await asyncio.to_thread(self._load_github_baseline, force)
        except (GitBaselineFetchError, BaselineParseError, ConfigurationError, OSError) as exc:
            message = str(exc)
            if self.baseline_error != message:
                logger.error("Baseline unavailable: {}", message)
                await asyncio.to_thread(
                    self.store.record_audit,
                    AuditAction.BASELINE_FETCH_FAILED,
                    self.settings.baseline_file_path,
                    message,
                )
            self.baseline_error = message
            return False
        if self.baseline_error is not None:
            logger.info("Baseline source recovered")
        self.baseline_error = None
        if changed:
            await self._on_baseline_changed()
        return changed

    def _install_baseline(self, baseline: Baseline) -> bool:
        """Swap in a parsed baseline; returns whether the content changed."""
        previous = self.baseline
        self.baseline = baseline
        if previous is not None and previous.sha == baseline.sha:
            previous.fetched_at = baseline.fetched_at
            self.baseline = previous
            return False
        self._baseline_changed_at = time.monotonic()
        self.store.record_audit(
            AuditAction.BASELINE_FETCHED,
            f"{baseline.project_name}",
            f"source={baseline.source.value} sha={(baseline.sha or '')[:12]} "
            f"services={baseline.service_names}",
        )
        return True

    def _load_mock_baseline(self, force: bool) -> bool:
        path = Path(self.settings.mock_baseline_path)
        raw = path.read_text(encoding="utf-8")
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        if not force and digest == self._mock_digest and self.baseline is not None:
            return False
        self._mock_digest = digest
        baseline = parse_baseline(
            raw,
            fallback_namespace=self.settings.compose_namespace,
            sha=digest,
            source=BaselineSource.MOCK,
        )
        return self._install_baseline(baseline)

    def _load_github_baseline(self, force: bool) -> bool:
        if self.github is None:
            raise ConfigurationError("GitHub client is not configured")
        fetched = self.github.fetch_baseline(None if force else self._etag)
        if fetched is None:
            if self.baseline is not None:
                self.baseline.fetched_at = datetime.now(timezone.utc)
            return False
        self._etag = fetched.etag
        baseline = parse_baseline(
            fetched.content,
            fallback_namespace=self.settings.compose_namespace,
            sha=fetched.sha,
            source=BaselineSource.GITHUB,
        )
        return self._install_baseline(baseline)

    async def _on_baseline_changed(self) -> None:
        """Clear buffered adoptions the new baseline now declares, then reconcile."""
        assert self.baseline is not None
        for pending in list(self._pending.values()):
            if pending.service_name in self.baseline.services:
                await asyncio.to_thread(self.store.clear_pending, pending.id, "baseline_updated")
                await asyncio.to_thread(
                    self.store.record_audit,
                    AuditAction.ADOPTION_CLEARED,
                    pending.container_name,
                    f"service {pending.service_name} now declared in baseline",
                )
                self._pending.pop(pending.container_id, None)
        if self.startup_done:
            self.schedule_reconcile("baseline")

    # ---------------------------------------------------------------- loops

    async def _baseline_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.settings.baseline_poll_interval_seconds)
            try:
                await self.refresh_baseline()
            except Exception as exc:  # never let the poller die
                logger.exception("Baseline poll crashed: {}", exc)

    async def _sweep_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.settings.reconcile_sweep_interval_seconds)
            try:
                await self.reconcile("sweep")
            except Exception as exc:
                logger.exception("Periodic sweep crashed: {}", exc)

    async def _event_loop(self) -> None:
        """Consume Docker events with one global debounce window."""
        while not self._stop.is_set():
            event = await self._queue.get()
            if self._is_own_action(event):
                logger.debug("Ignoring own action event {} {}", event.action, event.name)
                continue
            logger.info(
                "Docker event {} on {} ({})",
                event.action,
                event.name or "?",
                event.container_id[:12],
            )
            deadline = time.monotonic() + self.settings.drift_debounce_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    extra = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if not self._is_own_action(extra):
                    logger.debug("Coalesced event {} on {}", extra.action, extra.name)
            try:
                await self.reconcile("event")
            except Exception as exc:
                logger.exception("Event reconcile crashed: {}", exc)

    def schedule_reconcile(self, trigger: str) -> None:
        """Run a reconcile cycle soon without blocking the caller."""
        asyncio.get_running_loop().create_task(self._safe_reconcile(trigger))

    async def _safe_reconcile(self, trigger: str) -> None:
        try:
            await self.reconcile(trigger)
        except Exception as exc:
            logger.exception("Reconcile ({}) crashed: {}", trigger, exc)

    # ------------------------------------------------------- self-action cache

    def _remember_action(self, *keys: str | None) -> None:
        expiry = time.monotonic() + ACTION_CACHE_TTL_SECONDS
        for key in keys:
            if key:
                self._action_cache[key] = expiry

    def _is_own_action(self, event: RawDockerEvent) -> bool:
        now = time.monotonic()
        for key, expiry in list(self._action_cache.items()):
            if expiry < now:
                del self._action_cache[key]
        candidates = (event.container_id, event.name, event.attributes.get(WARDEN_ACTION_LABEL))
        return any(key and key in self._action_cache for key in candidates)

    # -------------------------------------------------------------- lookups

    def _image_environment(self, image_id: str) -> dict[str, str]:
        if image_id not in self._image_env_cache:
            self._image_env_cache[image_id] = self.watcher.image_default_environment(image_id)
            self._image_labels_cache[image_id] = self.watcher.image_default_labels(image_id)
        return self._image_env_cache[image_id]

    def _image_labels(self, image_id: str) -> dict[str, str]:
        if image_id not in self._image_labels_cache:
            self._image_environment(image_id)
        return self._image_labels_cache[image_id]

    def _context(self) -> EvaluationContext:
        return EvaluationContext(
            baseline=self.baseline,
            hostname=self.settings.hostname,
            excluded_prefixes=self.settings.excluded_prefixes,
            self_container_id=self.watcher.self_container_id,
            whitelist=self._whitelist,
            pending=dict(self._pending),
            image_environment=self._image_environment,
            image_labels=self._image_labels,
        )

    # ------------------------------------------------------------ reconcile

    async def reconcile(self, trigger: str) -> HostSnapshot:
        """Run one full cycle: inspect, evaluate, persist transitions, enforce.

        Args:
            trigger: What started the cycle, recorded on the snapshot.

        Returns:
            The published snapshot.
        """
        async with self._cycle_lock:
            await self._expire_pending()
            containers: list[LiveContainer] = []
            try:
                containers = await asyncio.to_thread(self.watcher.list_containers)
                self.docker_connected = True
                self.docker_error = None
            except DockerConnectionError as exc:
                self.docker_connected = False
                self.docker_error = str(exc)
                logger.warning("Docker unavailable during {} cycle: {}", trigger, exc)

            # Image lookups touch Docker; run the evaluation off the loop.
            evaluation = await asyncio.to_thread(evaluate, containers, self._context())
            snapshot = HostSnapshot(
                hostname=self.settings.hostname,
                taken_at=datetime.now(timezone.utc),
                trigger=trigger,
                mode=self.settings.warden_mode,
                enforcement_active=self.enforcement_active,
                docker_connected=self.docker_connected,
                docker_error=self.docker_error,
                baseline=self.baseline_info(),
                records=evaluation.records if self.docker_connected else [],
                exempt=evaluation.exempt,
            )
            transitions = self.projector.transitions(snapshot)
            self.projector.publish(snapshot)
            await asyncio.to_thread(self._persist_transitions, transitions)
            logger.info(
                "Reconcile ({}) {} containers -> {}",
                trigger,
                len(containers),
                {k: v for k, v in snapshot.counts().items() if v},
            )

        if snapshot.enforcement_active and trigger not in ("startup", "post-enforce"):
            acted = await self._enforce(snapshot)
            if acted:
                self.schedule_reconcile("post-enforce")
        return snapshot

    def _persist_transitions(
        self, transitions: list[tuple[DriftRecord, DriftStatus | None]]
    ) -> None:
        detection_actions = {
            DriftStatus.ROGUE: AuditAction.ROGUE_DETECTED,
            DriftStatus.DRIFTED: AuditAction.DRIFT_DETECTED,
            DriftStatus.MISSING: AuditAction.MISSING_DETECTED,
        }
        for record, previous in transitions:
            details = record.message
            if record.changed_fields:
                details = f"{record.message} Changed: {', '.join(record.changed_fields)}."
            self.store.record_drift_event(
                ref=record.ref,
                drift_status=record.drift_status,
                previous_status=previous,
                service_name=record.service_name,
                container_name=record.container_name,
                details=details,
            )
            action = detection_actions.get(record.drift_status)
            if action is not None:
                self.store.record_audit(action, record.ref, details)
            elif record.drift_status is DriftStatus.MATCHED and previous is not None:
                self.store.record_audit(
                    AuditAction.STATE_RESTORED, record.ref, f"was {previous.value}"
                )

    async def _expire_pending(self) -> None:
        now = datetime.now(timezone.utc)
        for pending in list(self._pending.values()):
            if pending.expires_at <= now:
                await asyncio.to_thread(self.store.clear_pending, pending.id, "expired")
                await asyncio.to_thread(
                    self.store.record_audit,
                    AuditAction.ADOPTION_EXPIRED,
                    pending.container_name,
                    f"TTL elapsed before baseline declared {pending.service_name}",
                )
                self._pending.pop(pending.container_id, None)

    # ---------------------------------------------------------- enforcement

    async def _enforce(self, snapshot: HostSnapshot) -> bool:
        """Autonomously remediate everything actionable in a snapshot.

        Before the first destructive step the baseline is re-fetched; if it
        changed, this pass is abandoned and the next cycle re-evaluates.
        """
        targets = [
            r
            for r in snapshot.records
            if r.drift_status in (DriftStatus.ROGUE, DriftStatus.DRIFTED, DriftStatus.MISSING)
        ]
        if not targets:
            return False
        if not self.settings.allow_mock_baseline and await self.refresh_baseline(force=True):
            logger.info("Baseline changed right before enforcement; deferring to next cycle")
            return False
        if not self.enforcement_active:
            return False
        acted = False
        now = time.monotonic()
        for record in targets:
            last = self._enforced_at.get(record.ref)
            if last is not None and now - last < ENFORCEMENT_COOLDOWN_SECONDS:
                logger.warning(
                    "Skipping enforcement of {}: acted {:.0f}s ago and it is still {}",
                    record.ref,
                    now - last,
                    record.drift_status.value,
                )
                continue
            self._enforced_at[record.ref] = now
            try:
                if record.drift_status is DriftStatus.ROGUE:
                    await self._kill_record(record, autonomous=True)
                else:
                    await self._revert_record(record, autonomous=True)
                acted = True
            except DriftWardenError as exc:
                logger.error("Enforcement failed for {}: {}", record.ref, exc)
        return acted

    # -------------------------------------------------------------- actions

    def _find(self, ref: str) -> DriftRecord:
        record = self.projector.snapshot.find(ref)
        if record is None:
            raise DriftWardenError(f"No container or service matches '{ref}'")
        return record

    async def revert(self, ref: str) -> ActionResult:
        """Recreate a drifted or missing service from the baseline (manual Revert)."""
        record = self._find(ref)
        if record.service_name is None:
            raise DriftWardenError(f"'{ref}' is not a baseline service; use Enforce Kill or Adopt")
        if record.drift_status is DriftStatus.PENDING_ADOPTION:
            raise DriftWardenError(f"'{ref}' is buffered for adoption and exempt from enforcement")
        await asyncio.to_thread(
            self.store.record_audit, AuditAction.REVERT_REQUESTED, record.ref, record.message
        )
        return await self._revert_record(record, autonomous=False)

    async def _revert_record(self, record: DriftRecord, *, autonomous: bool) -> ActionResult:
        if self.baseline is None or record.service_name is None:
            raise DriftWardenError("No baseline available to revert to")
        service = self.baseline.services.get(record.service_name)
        if service is None:
            raise DriftWardenError(f"Service {record.service_name} is no longer in the baseline")
        action_id = uuid.uuid4().hex[:12]
        target_name = (
            service.container_name or f"{self.baseline.project_name}-{service.service_name}-1"
        )
        self._remember_action(record.container_id, record.container_name, target_name, action_id)
        try:
            if record.container_id and record.container_name != target_name:
                await asyncio.to_thread(self.watcher.stop_and_remove, record.container_id)
            new_id = await asyncio.to_thread(
                self.watcher.recreate,
                service,
                self.baseline.project_name,
                action_id=action_id,
                declared_volumes=self.baseline.declared_volumes,
            )
        except (ContainerRecreateError, RogueContainerKillError, DockerConnectionError) as exc:
            await asyncio.to_thread(
                self.store.record_audit, AuditAction.REVERT_FAILED, record.ref, str(exc)
            )
            raise
        self._remember_action(new_id)
        await asyncio.to_thread(
            self.store.record_audit,
            AuditAction.REVERT_COMPLETED,
            record.ref,
            f"{'autonomous' if autonomous else 'manual'} recreate of {service.service_name} "
            f"as {target_name} ({new_id[:12]})",
        )
        if not autonomous:
            self.schedule_reconcile("revert")
        return ActionResult(
            ok=True,
            action="revert",
            target=record.ref,
            message=f"Recreated {service.service_name} as {target_name} from the Git baseline.",
        )

    async def kill(self, ref: str) -> ActionResult:
        """Stop and remove a rogue container (manual Enforce Kill)."""
        record = self._find(ref)
        if (
            record.drift_status not in (DriftStatus.ROGUE, DriftStatus.MISSING)
            or not record.container_id
        ):
            raise DriftWardenError(f"'{ref}' is not a rogue container")
        return await self._kill_record(record, autonomous=False)

    async def _kill_record(self, record: DriftRecord, *, autonomous: bool) -> ActionResult:
        if not record.container_id:
            raise DriftWardenError(f"'{record.ref}' has no container to remove")
        self._remember_action(record.container_id, record.container_name)
        try:
            await asyncio.to_thread(self.watcher.stop_and_remove, record.container_id)
        except (RogueContainerKillError, DockerConnectionError) as exc:
            await asyncio.to_thread(
                self.store.record_audit, AuditAction.ENFORCE_KILL_FAILED, record.ref, str(exc)
            )
            raise
        await asyncio.to_thread(
            self.store.record_audit,
            AuditAction.ENFORCE_KILL,
            record.ref,
            f"{'autonomous' if autonomous else 'manual'} removal of {record.container_name} "
            f"({record.image})",
        )
        if not autonomous:
            self.schedule_reconcile("kill")
        return ActionResult(
            ok=True,
            action="kill",
            target=record.ref,
            message=f"Stopped and removed {record.container_name}.",
        )

    async def adopt(self, ref: str, note: str | None = None) -> ActionResult:
        """Open a Reverse-GitOps pull request for a rogue or drifted container."""
        record = self._find(ref)
        if record.drift_status not in (DriftStatus.ROGUE, DriftStatus.DRIFTED):
            raise DriftWardenError(
                f"'{ref}' is {record.drift_status.value}; "
                "only rogue or drifted containers can be adopted"
            )
        if self.github is None or self.settings.allow_mock_baseline:
            raise AdoptionError("Adoption needs GitHub; ALLOW_MOCK_BASELINE is enabled")
        if self.baseline is None or not record.container_id:
            raise AdoptionError("No baseline or container available for adoption")
        container = await asyncio.to_thread(self.watcher.inspect, record.container_id)
        if container is None:
            raise AdoptionError(f"Container {record.ref} disappeared before adoption")

        project = self.baseline.project_name
        service_name = record.service_name or sanitise_service_name(container.name, project)
        observed = observed_service(container, service_name, project, self._context())
        stripped = project_networks(container, project)
        external = {
            name: name
            for name in stripped
            if name not in ("default", "bridge", "host", "none") and name in container.networks
        }
        spec = AdoptionSpec(
            service_name=service_name,
            image=container.image,
            container_name=container.name if record.drift_status is DriftStatus.ROGUE else None,
            ports=observed.ports,
            networks=[n for n in observed.networks if n not in ("bridge", "host", "none")],
            external_networks=external,
            labels=observed.labels,
            restart=observed.restart,
            environment_keys=list(observed.environment),
        )
        changed_fields = record.changed_fields
        is_update = record.drift_status is DriftStatus.DRIFTED
        final_name = service_name

        def mutate(latest: str) -> str:
            nonlocal final_name
            try:
                if is_update:
                    text, final_name = update_service(latest, spec, changed_fields)
                else:
                    text, final_name = add_service(latest, spec)
            except YAMLMutationError as exc:
                raise AdoptionError(str(exc)) from exc
            return text

        await asyncio.to_thread(
            self.store.record_audit, AuditAction.ADOPT_REQUESTED, record.ref, note or record.message
        )
        title = (
            f"warden: adopt {container.name} as service {service_name}"
            if not is_update
            else f"warden: sync {service_name} with live container {container.name}"
        )
        body = _pr_body(record, container, spec, note, self.settings.hostname, is_update)
        try:
            pull: AdoptionPullRequest = await asyncio.to_thread(
                self.github.open_adoption_pr,
                container_name=container.name,
                service_name=service_name,
                mutate=mutate,
                title=title,
                body=body,
            )
        except AdoptionError as exc:
            await asyncio.to_thread(
                self.store.record_audit, AuditAction.ADOPT_FAILED, record.ref, str(exc)
            )
            raise
        pending = await asyncio.to_thread(
            self.store.add_pending,
            container_id=container.id,
            container_name=container.name,
            service_name=final_name,
            ttl_seconds=self.settings.adoption_buffer_ttl_seconds,
            pr_number=pull.number,
            pr_url=pull.url,
            branch=pull.branch,
        )
        self._pending[container.id] = pending
        await asyncio.to_thread(
            self.store.record_audit,
            AuditAction.ADOPT_PR_OPENED,
            record.ref,
            f"PR #{pull.number} {pull.url}{' (reused open PR)' if pull.reused else ''}",
        )
        self.schedule_reconcile("adopt")
        verb = "Reusing open" if pull.reused else "Opened"
        return ActionResult(
            ok=True,
            action="adopt",
            target=record.ref,
            message=f"{verb} pull request #{pull.number} adding {final_name} to the baseline.",
            pr_url=pull.url,
        )

    async def whitelist(self, request: WhitelistRequest) -> WhitelistEntry:
        """Exempt a container or project and re-evaluate the host."""
        entry = await asyncio.to_thread(
            self.store.add_whitelist, request.target_type, request.target_identifier, request.reason
        )
        await asyncio.to_thread(
            self.store.record_audit,
            AuditAction.WHITELIST_ADDED,
            request.target_identifier,
            f"{request.target_type.value}: {request.reason or 'no reason given'}",
        )
        await asyncio.to_thread(self._reload_durable_state)
        self.schedule_reconcile("whitelist")
        return entry

    async def unwhitelist(self, entry_id: int) -> WhitelistEntry | None:
        """Remove an exemption and re-evaluate the host."""
        entry = await asyncio.to_thread(self.store.remove_whitelist, entry_id)
        if entry is not None:
            await asyncio.to_thread(
                self.store.record_audit,
                AuditAction.WHITELIST_REMOVED,
                entry.target_identifier,
                entry.target_type.value,
            )
            await asyncio.to_thread(self._reload_durable_state)
            self.schedule_reconcile("whitelist")
        return entry

    @property
    def pending(self) -> list[PendingAdoption]:
        """Active adoption buffer entries."""
        return list(self._pending.values())


def _pr_body(
    record: DriftRecord,
    container: LiveContainer,
    spec: AdoptionSpec,
    note: str | None,
    hostname: str,
    is_update: bool,
) -> str:
    """Compose the Markdown body of an adoption pull request."""
    lines = [
        "## DriftWarden Reverse-GitOps adoption",
        "",
        f"**Host:** `{hostname}`  ",
        f"**Container:** `{container.name}` (`{container.short_id}`)  ",
        f"**Image:** `{container.image}`  ",
        f"**Finding:** {record.drift_status.value.upper()}: {record.message}",
        "",
    ]
    if is_update and record.changed_fields:
        lines += ["### Fields synced from the live container", ""]
        for diff in record.diffs:
            if diff.changed:
                lines.append(f"- **{diff.field}**: `{diff.expected}` → `{diff.observed}`")
        lines.append("")
    else:
        lines += [f"Adds service `{spec.service_name}` so this workload is declared in Git.", ""]
    if spec.environment_keys:
        lines += [
            "### Environment",
            "",
            "Values were **not** committed. The following keys are written as `${KEY}` "
            "placeholders and must be supplied by the deployment environment:",
            "",
        ]
        lines += [f"- `{key}`" for key in spec.environment_keys]
        lines.append("")
    if note:
        lines += ["### Operator note", "", note, ""]
    lines += ["---", "_Generated by DriftWarden from the live Docker socket state._"]
    return "\n".join(lines)
