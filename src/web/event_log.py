"""Events Log presentation: badges, filters, secret masking and CSV / JSON export.

The Events page and the export endpoints share one :class:`EventFilter`, so an
export always contains exactly the rows the operator is looking at (without the
page's display limit).
"""

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from typing import Any
from urllib.parse import urlencode

from markupsafe import Markup, escape

from shared.constants import AuditAction, Severity
from shared.models import AuditLogEntry

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


def audit_meta(action: str) -> dict[str, str]:
    """Badge metadata for an action value, with a neutral fallback for unknown values."""
    return AUDIT_META.get(
        action, {"label": action.replace("_", " ").title(), "tone": "slate", "category": "actions"}
    )


SEVERITY_META: dict[str, dict[str, str]] = {
    Severity.CRITICAL.value: {"label": "Critical", "tone": "rose"},
    Severity.HIGH.value: {"label": "High", "tone": "orange"},
    Severity.MEDIUM.value: {"label": "Medium", "tone": "amber"},
    Severity.LOW.value: {"label": "Low", "tone": "sky"},
    Severity.INFO.value: {"label": "Info", "tone": "slate"},
}
"""Severity badge label and colour family, most severe first."""

RESULT_META: dict[str, dict[str, str]] = {
    "success": {"label": "Success", "tone": "emerald"},
    "failed": {"label": "Failed", "tone": "rose"},
    "pending": {"label": "Pending", "tone": "violet"},
}
"""RESULT badge label and colour family."""

PAGE_LIMIT = 200
"""Rows shown per table on the Events page."""

SCAN_LIMIT = 5000
"""Rows read from the database before the page's filters apply."""

EXPORT_LIMIT = 50000
"""Upper bound of rows in one export."""

_CORRELATION = re.compile(r"^INC-\d+$", re.IGNORECASE)

# ----------------------------------------------------------------- filters


def parse_utc(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM[:SS]`` as a UTC instant.

    A bare date means the start of that day, or its last microsecond when
    ``end_of_day`` is set (so an *until* date includes the whole day). Anything
    unparsable is ignored rather than rejected.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime.combine(day, time.max if end_of_day else time.min, timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class EventFilter:
    """The Events page filters, parsed from (and written back to) query parameters.

    Attributes:
        category: Audit pill: ``all``, ``actions``, ``detections`` or ``reversals``.
        severity: Exact severity value, or ``None`` for any.
        since: Inclusive lower bound (UTC).
        until: Inclusive upper bound (UTC).
        query: Free-text search: container or service name, ``INC-n`` or an event id.
        raw_since: ``since`` as typed, for the form.
        raw_until: ``until`` as typed, for the form.
    """

    category: str = "all"
    severity: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    query: str = ""
    raw_since: str = ""
    raw_until: str = ""

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> "EventFilter":
        """Build a filter from request query parameters; unknown values are ignored."""
        category = params.get("category", "all")
        if category not in {key for key, _ in AUDIT_CATEGORIES}:
            category = "all"
        severity = (params.get("severity") or "").lower() or None
        if severity not in SEVERITY_META:
            severity = None
        raw_since = (params.get("since") or "").strip()
        raw_until = (params.get("until") or "").strip()
        return cls(
            category=category,
            severity=severity,
            since=parse_utc(raw_since),
            until=parse_utc(raw_until, end_of_day=True),
            query=(params.get("q") or "").strip()[:200],
            raw_since=raw_since if parse_utc(raw_since) else "",
            raw_until=raw_until if parse_utc(raw_until) else "",
        )

    @property
    def correlation(self) -> str | None:
        """The incident id when the search is exactly one (``INC-007``)."""
        return self.query.upper() if _CORRELATION.match(self.query) else None

    @property
    def active(self) -> bool:
        """Whether anything narrows the rows (the category pill aside)."""
        return bool(self.severity or self.since or self.until or self.query)

    def params(self, **overrides: str) -> dict[str, str]:
        """Non-default parameters, for links that keep the current filters."""
        values = {
            "category": self.category if self.category != "all" else "",
            "severity": self.severity or "",
            "since": self.raw_since,
            "until": self.raw_until,
            "q": self.query,
        }
        values.update(overrides)
        return {k: v for k, v in values.items() if v and not (k == "category" and v == "all")}

    def query_string(self, **overrides: str) -> str:
        """``?a=b`` for the current filters with overrides, or an empty string."""
        encoded = urlencode(self.params(**overrides))
        return f"?{encoded}" if encoded else ""

    def without_category(self) -> "EventFilter":
        """The same filter on every audit category (for the pill counts)."""
        return replace(self, category="all")

    def _matches_search(self, row_id: int, fields: Iterable[object]) -> bool:
        query = self.query.lower()
        if not query:
            return True
        if query.lstrip("#").isdigit() and int(query.lstrip("#")) == row_id:
            return True
        return any(query in str(value).lower() for value in fields if value)

    def match_audit(self, entry: AuditLogEntry) -> bool:
        """Whether an audit entry passes every filter."""
        if self.category != "all" and audit_meta(entry.action.value)["category"] != self.category:
            return False
        if self.severity and entry.severity.value != self.severity:
            return False
        if self.since and entry.timestamp < self.since:
            return False
        if self.until and entry.timestamp > self.until:
            return False
        if self.correlation:
            return (entry.correlation_id or "").upper() == self.correlation
        return self._matches_search(
            entry.id,
            (
                entry.target,
                entry.correlation_id,
                entry.details,
                json.dumps(entry.details_json) if entry.details_json else None,
            ),
        )

    def match_event(self, event: Mapping[str, Any]) -> bool:
        """Whether a drift transition row (``Store.list_drift_events``) passes the filters."""
        if self.severity and event.get("severity") != self.severity:
            return False
        detected = event.get("detected_at")
        if isinstance(detected, datetime):
            if self.since and detected < self.since:
                return False
            if self.until and detected > self.until:
                return False
        if self.correlation:
            return str(event.get("correlation_id") or "").upper() == self.correlation
        details_json = event.get("details_json")
        return self._matches_search(
            int(event.get("id") or 0),
            (
                event.get("ref"),
                event.get("service_name"),
                event.get("container_name"),
                event.get("correlation_id"),
                json.dumps(details_json) if details_json else None,
            ),
        )


# ----------------------------------------------------------------- masking

MASK = "••••••"
"""Replacement for a secret value."""

_SECRET_WORDS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "private_key",
    "privatekey",
    "credential",
    "auth",
    "cookie",
    "access_key",
    "accesskey",
)
_SECRET_SEGMENT = re.compile(r"(^|[_\-.])(pat|pwd|pass|dsn|salt)($|[_\-.])")
_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.\-]*)=([^\s,;]+)")


def is_secret_key(key: str) -> bool:
    """Whether a key or environment variable name looks like it holds a secret."""
    lowered = key.lower()
    return any(word in lowered for word in _SECRET_WORDS) or bool(_SECRET_SEGMENT.search(lowered))


def _mask_text(text: str) -> str:
    return _ASSIGNMENT.sub(
        lambda m: f"{m.group(1)}={MASK}" if is_secret_key(m.group(1)) else m.group(0), text
    )


def mask_secrets(value: Any, key: str | None = None) -> Any:
    """Copy of ``value`` with secret-looking values replaced by :data:`MASK`.

    Dictionary entries whose key looks secret are masked whole (unless they hold a
    nested structure, which is walked), and ``NAME=value`` pairs inside strings are
    masked when ``NAME`` looks secret, so environment lists and diff text are safe.
    """
    if isinstance(value, Mapping):
        return {str(k): mask_secrets(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_secrets(item, key) for item in value]
    if key is not None and is_secret_key(key) and value not in (None, ""):
        return MASK
    if isinstance(value, str):
        return _mask_text(value)
    return value


# --------------------------------------------------------------- rendering


def _label(key: str) -> str:
    text = key.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else key


def render_details(value: Any) -> Markup:
    """Render structured metadata generically as HTML (secrets masked first).

    Mappings become a key/value grid, lists of mappings a table (an expected vs
    observed diff renders as one), other lists a bullet list, and scalars text.
    """
    return _render(mask_secrets(value))


def _render(value: Any) -> Markup:
    if isinstance(value, Mapping):
        if not value:
            return Markup('<span class="text-slate-500">(empty)</span>')
        items = "".join(
            f'<dt class="text-slate-500">{escape(_label(str(k)))}</dt><dd>{_render(v)}</dd>'
            for k, v in value.items()
        )
        return Markup(f'<dl class="kv-grid">{items}</dl>')
    if isinstance(value, list):
        if not value:
            return Markup('<span class="text-slate-500">(none)</span>')
        if all(isinstance(item, Mapping) for item in value):
            columns: list[str] = []
            for item in value:
                for k in item:
                    if str(k) not in columns:
                        columns.append(str(k))
            head = "".join(f"<th>{escape(_label(c))}</th>" for c in columns)
            body = "".join(
                "<tr"
                + (' class="changed"' if item.get("changed") is True else "")
                + ">"
                + "".join(f"<td>{_render(item.get(c, ''))}</td>" for c in columns)
                + "</tr>"
                for item in value
            )
            return Markup(
                f'<table class="detail-table"><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table>"
            )
        items = "".join(f"<li>{_render(item)}</li>" for item in value)
        return Markup(f'<ul class="detail-list">{items}</ul>')
    if value is None or value == "":
        return Markup('<span class="text-slate-500">—</span>')
    if isinstance(value, bool):
        return Markup("yes" if value else "no")
    return Markup(f'<span class="font-mono break-anywhere">{escape(str(value))}</span>')


# ------------------------------------------------------------------- export

AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "timestamp",
    "action",
    "label",
    "category",
    "severity",
    "result",
    "correlation_id",
    "target",
    "hostname",
    "details",
    "reverses_id",
    "reversed_by_id",
    "details_json",
)
"""CSV columns of an audit log export, in order."""

EVENT_COLUMNS: tuple[str, ...] = (
    "id",
    "detected_at",
    "ref",
    "previous_status",
    "drift_status",
    "severity",
    "result",
    "trigger",
    "reason",
    "correlation_id",
    "service_name",
    "container_name",
    "hostname",
    "details",
    "details_json",
)
"""CSV columns of a drift transitions export, in order."""


def _iso(value: object) -> object:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def audit_export_rows(entries: Iterable[AuditLogEntry]) -> list[dict[str, Any]]:
    """Audit entries as plain dictionaries (UTC ISO timestamps, secrets masked)."""
    rows: list[dict[str, Any]] = []
    for entry in entries:
        meta = audit_meta(entry.action.value)
        rows.append(
            {
                "id": entry.id,
                "timestamp": _iso(entry.timestamp),
                "action": entry.action.value,
                "label": meta["label"],
                "category": meta["category"],
                "severity": entry.severity.value,
                "result": entry.result.value if entry.result else None,
                "correlation_id": entry.correlation_id,
                "target": entry.target,
                "hostname": entry.hostname,
                "details": mask_secrets(entry.details),
                "reverses_id": entry.reverses_id,
                "reversed_by_id": entry.reversed_by_id,
                "details_json": mask_secrets(entry.details_json),
            }
        )
    return rows


def event_export_rows(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drift transitions as plain dictionaries (UTC ISO timestamps, secrets masked)."""
    return [
        {
            column: (
                mask_secrets(event.get(column))
                if column in ("details", "details_json")
                else _iso(event.get(column))
            )
            for column in EVENT_COLUMNS
        }
        for event in events
    ]


def to_csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    """CSV text with a header row; nested metadata is written as compact JSON."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: (
                    json.dumps(value, separators=(",", ":"), default=str)
                    if isinstance(value, (dict, list))
                    else ("" if value is None else value)
                )
                for key, value in row.items()
            }
        )
    return buffer.getvalue()


def to_json(rows: list[dict[str, Any]], table: str, event_filter: EventFilter) -> str:
    """JSON document: export metadata (table, filters, UTC time) and the rows."""
    document = {
        "table": table,
        "exported_at": _iso(datetime.now(timezone.utc)),
        "filters": {
            "category": event_filter.category if table == "audit" else None,
            "severity": event_filter.severity,
            "since": _iso(event_filter.since),
            "until": _iso(event_filter.until),
            "q": event_filter.query or None,
        },
        "count": len(rows),
        "rows": rows,
    }
    return json.dumps(document, indent=2, ensure_ascii=False, default=str)
