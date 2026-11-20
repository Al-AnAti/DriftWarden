"""Server-rendered pages: Dashboard, Diff, Events Log, State Buffer, Cluster Config."""

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from shared.constants import REVERSIBLE_ACTIONS, AuditAction, DriftStatus
from shared.models import DriftRecord
from warden.engine.state_projector import snapshot_payload
from web.runtime import WardenRuntime, get_runtime

router = APIRouter(tags=["Web UI Views"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

STATUS_META: dict[str, dict[str, str]] = {
    DriftStatus.MATCHED.value: {"label": "Matched", "tone": "emerald"},
    DriftStatus.DRIFTED.value: {"label": "Drifted", "tone": "amber"},
    DriftStatus.MISSING.value: {"label": "Missing", "tone": "sky"},
    DriftStatus.ROGUE.value: {"label": "Rogue", "tone": "rose"},
    DriftStatus.PENDING_ADOPTION.value: {"label": "Pending adoption", "tone": "violet"},
    DriftStatus.AWAITING_RESTART.value: {"label": "Awaiting restart", "tone": "orange"},
}
"""Display label and Tailwind colour family per status."""

OVERALL_META: dict[str, dict[str, str]] = {
    "in_sync": {"label": "IN SYNC", "tone": "emerald"},
    "missing": {"label": "SERVICE MISSING", "tone": "sky"},
    "awaiting_restart": {"label": "AWAITING RESTART", "tone": "orange"},
    "drift_detected": {"label": "DRIFT DETECTED", "tone": "amber"},
    "docker_unavailable": {"label": "DOCKER UNAVAILABLE", "tone": "rose"},
    "baseline_unavailable": {"label": "BASELINE_UNAVAILABLE", "tone": "rose"},
}
"""Headline pill text and colour per overall status."""

AUDIT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("actions", "Actions"),
    ("detections", "Detections"),
    ("reversals", "Reversals"),
)
"""Filter pills above the audit log: key and label."""

_A = AuditAction
AUDIT_META: dict[str, dict[str, str]] = {
    _A.ROGUE_DETECTED.value: {"label": "Rogue Detected", "tone": "rose", "category": "detections"},
    _A.DRIFT_DETECTED.value: {"label": "Drift Detected", "tone": "amber", "category": "detections"},
    _A.MISSING_DETECTED.value: {
        "label": "Missing Detected",
        "tone": "sky",
        "category": "detections",
    },
    _A.STATE_RESTORED.value: {
        "label": "State Restored",
        "tone": "emerald",
        "category": "detections",
    },
    _A.REVERT_COMPLETED.value: {
        "label": "Enforced (Recreated)",
        "tone": "emerald",
        "category": "actions",
    },
    _A.ENFORCE_KILL.value: {
        "label": "Enforced (Removed)",
        "tone": "emerald",
        "category": "actions",
    },
    _A.REVERT_FAILED.value: {
        "label": "Enforce Failed (Recreate)",
        "tone": "rose",
        "category": "actions",
    },
    _A.ENFORCE_KILL_FAILED.value: {
        "label": "Enforce Failed (Remove)",
        "tone": "rose",
        "category": "actions",
    },
    _A.REVERT_REQUESTED.value: {
        "label": "Enforce Requested",
        "tone": "slate",
        "category": "actions",
    },
    _A.ADOPT_REQUESTED.value: {"label": "Adopt Requested", "tone": "slate", "category": "actions"},
    _A.ADOPT_PR_OPENED.value: {"label": "PR Opened", "tone": "violet", "category": "actions"},
    _A.ADOPT_FAILED.value: {"label": "Adopt Failed", "tone": "rose", "category": "actions"},
    _A.ADOPTION_MERGED.value: {
        "label": "Adoption Merged",
        "tone": "emerald",
        "category": "actions",
    },
    _A.ADOPTION_CLEARED.value: {
        "label": "Adoption Cleared",
        "tone": "emerald",
        "category": "actions",
    },
    _A.ADOPTION_CLOSED.value: {"label": "PR Closed", "tone": "slate", "category": "actions"},
    _A.ADOPTION_EXPIRED.value: {
        "label": "Adoption Expired",
        "tone": "orange",
        "category": "actions",
    },
    _A.ADOPTION_INVALIDATED.value: {
        "label": "Adoption Invalidated",
        "tone": "orange",
        "category": "actions",
    },
    _A.ADOPTION_REVOKED.value: {
        "label": "Adoption Revoked",
        "tone": "slate",
        "category": "actions",
    },
    _A.REVERSAL_COMPLETED.value: {"label": "Reversed", "tone": "indigo", "category": "reversals"},
    _A.REVERSAL_FAILED.value: {"label": "Reversal Failed", "tone": "rose", "category": "reversals"},
    _A.REVERSAL_REFUSED.value: {
        "label": "Reversal Refused",
        "tone": "orange",
        "category": "reversals",
    },
    _A.WHITELIST_ADDED.value: {"label": "Whitelisted", "tone": "slate", "category": "actions"},
    _A.WHITELIST_REMOVED.value: {
        "label": "Whitelist Removed",
        "tone": "slate",
        "category": "actions",
    },
    _A.SETTINGS_CHANGED.value: {
        "label": "Settings Changed",
        "tone": "slate",
        "category": "actions",
    },
    _A.BATCH_REQUESTED.value: {"label": "Batch Requested", "tone": "slate", "category": "actions"},
    _A.ENGINE_STARTED.value: {"label": "Engine Started", "tone": "slate", "category": "actions"},
    _A.BASELINE_FETCHED.value: {
        "label": "Baseline Fetched",
        "tone": "slate",
        "category": "actions",
    },
    _A.BASELINE_FETCH_FAILED.value: {
        "label": "Baseline Fetch Failed",
        "tone": "rose",
        "category": "actions",
    },
}
"""Badge label, colour family and filter category per audit action."""

_PR_REFERENCE = re.compile(r"PR #(\d+)(?: https://github\.com/[^\s]+/pull/\d+)?")


def pr_links(text: str | None, base_url: str | None) -> Markup:
    """Escape ``text`` and turn every ``PR #N`` into a link to that pull request.

    A raw pull request URL right after the reference (older rows wrote one) is folded
    into the link. Without a GitHub repository the references stay plain text.

    Args:
        text: Audit detail text.
        base_url: ``https://github.com/<owner>/<repo>/pull/``, or ``None``.

    Returns:
        Safe HTML for the template.
    """
    if not text:
        return Markup("")
    out: list[str] = []
    last = 0
    for match in _PR_REFERENCE.finditer(text):
        out.append(str(escape(text[last : match.start()])))
        number = match.group(1)
        if base_url:
            out.append(
                f'<a class="text-indigo-300 hover:underline" href="{escape(base_url)}{number}" '
                f'target="_blank" rel="noopener noreferrer">PR #{number}</a>'
            )
        else:
            out.append(f"PR #{number}")
        last = match.end()
    out.append(str(escape(text[last:])))
    return Markup("".join(out))


templates.env.filters["pr_links"] = pr_links


def pull_base_url(target_repo: str | None, mock: bool) -> str | None:
    """Prefix of pull request URLs for the configured repository."""
    if mock or not target_repo:
        return None
    return f"https://github.com/{target_repo.strip().strip('/')}/pull/"


@dataclass(slots=True, frozen=True)
class DiffLine:
    """One rendered line of a diff pane.

    Attributes:
        number: 1-based line number within its own pane.
        sign: ``+`` for lines only in the observed state, ``-`` for lines only in
            the baseline, empty for lines present on both sides.
        text: The YAML line, indentation intact.
    """

    number: int
    sign: str
    text: str


def line_diff(expected: str | None, observed: str | None) -> tuple[list[DiffLine], list[DiffLine]]:
    """Line-level comparison of the two YAML panes.

    Only lines that genuinely differ get a marker, so a changed ``warden.repo``
    label is flagged without flagging its ``labels:`` parent.

    Args:
        expected: Baseline YAML, or ``None`` when the service is not declared.
        observed: Live YAML, or ``None`` when nothing is running.

    Returns:
        The baseline pane lines and the live pane lines.
    """
    left = expected.rstrip().splitlines() if expected else []
    right = observed.rstrip().splitlines() if observed else []
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    left_out: list[DiffLine] = []
    right_out: list[DiffLine] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        for index in range(i1, i2):
            left_out.append(DiffLine(index + 1, "" if tag == "equal" else "-", left[index]))
        for index in range(j1, j2):
            right_out.append(DiffLine(index + 1, "" if tag == "equal" else "+", right[index]))
    return left_out, right_out


STATUS_ORDER = [
    DriftStatus.ROGUE,
    DriftStatus.DRIFTED,
    DriftStatus.MISSING,
    DriftStatus.AWAITING_RESTART,
    DriftStatus.PENDING_ADOPTION,
    DriftStatus.MATCHED,
]


def base_context(request: Request, runtime: WardenRuntime, active: str) -> dict[str, Any]:
    """Context every page needs: snapshot, settings summary and nav state."""
    snapshot = runtime.projector.snapshot
    return {
        "request": request,
        "active": active,
        "snapshot": snapshot,
        "snapshot_json": snapshot_payload(snapshot),
        "counts": snapshot.counts(),
        "overall": snapshot.overall,
        "overall_meta": OVERALL_META,
        "status_meta": STATUS_META,
        "settings": runtime.settings,
        "hostname": runtime.settings.hostname,
        "mode": runtime.settings.warden_mode,
        "enforcement_active": runtime.reconciler.enforcement_active,
        "version": runtime.projector.version,
    }


def sorted_records(records: list[DriftRecord]) -> list[DriftRecord]:
    """Problems first, then matched services, each group alphabetical."""
    rank = {status: index for index, status in enumerate(STATUS_ORDER)}
    return sorted(records, key=lambda r: (rank[r.drift_status], r.ref.lower()))


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Live host state with counters and the container table."""
    runtime = get_runtime(request)
    context = base_context(request, runtime, "dashboard")
    context["records"] = sorted_records(runtime.projector.snapshot.records)
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.get("/diff/{ref}", response_class=HTMLResponse)
def diff_page(request: Request, ref: str) -> HTMLResponse:
    """Side-by-side Git baseline versus live observed state for one entry."""
    runtime = get_runtime(request)
    record = runtime.projector.snapshot.find(ref)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No container or service named '{ref}'")
    context = base_context(request, runtime, "dashboard")
    context["record"] = record
    context["changed_fields"] = set(record.changed_fields)
    expected_lines, observed_lines = line_diff(record.expected_yaml, record.observed_yaml)
    context["expected_lines"] = expected_lines
    context["observed_lines"] = observed_lines
    return templates.TemplateResponse(request, "diff.html", context)


@router.get("/events", response_class=HTMLResponse)
def events_page(request: Request) -> HTMLResponse:
    """Audit log and drift transitions."""
    runtime = get_runtime(request)
    context = base_context(request, runtime, "events")
    audit = runtime.store.list_audit(limit=200)
    context["audit"] = audit
    context["audit_meta"] = AUDIT_META
    context["audit_categories"] = AUDIT_CATEGORIES
    context["audit_counts"] = {
        key: sum(1 for a in audit if key == "all" or AUDIT_META[a.action.value]["category"] == key)
        for key, _ in AUDIT_CATEGORIES
    }
    context["pr_base_url"] = pull_base_url(
        runtime.settings.target_repo, runtime.settings.allow_mock_baseline
    )
    context["reversible_actions"] = sorted(a.value for a in REVERSIBLE_ACTIONS)
    context["events"] = runtime.store.list_drift_events(limit=200)
    return templates.TemplateResponse(request, "events.html", context)


@router.get("/buffer", response_class=HTMLResponse)
def buffer_page(request: Request) -> HTMLResponse:
    """Pending adoptions and their history."""
    runtime = get_runtime(request)
    context = base_context(request, runtime, "buffer")
    context["active"] = "buffer"
    context["pending"] = runtime.reconciler.pending
    context["history"] = runtime.store.pending_history(limit=100)
    return templates.TemplateResponse(request, "buffer.html", context)


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request) -> HTMLResponse:
    """Effective configuration, baseline YAML, exclusions and whitelist."""
    runtime = get_runtime(request)
    context = base_context(request, runtime, "config")
    context["whitelist"] = runtime.store.list_whitelist()
    context["db_counts"] = runtime.store.counts()
    context["baseline"] = runtime.projector.snapshot.baseline
    context["excluded_prefixes"] = runtime.settings.excluded_prefixes
    context["github_configured"] = runtime.settings.github_pat is not None
    context["editable_settings"] = runtime.reconciler.setting_views()
    return templates.TemplateResponse(request, "config.html", context)
