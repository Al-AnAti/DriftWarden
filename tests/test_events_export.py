"""Events page filters, detail drawers and CSV / JSON exports."""

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import web.app as web_app
from shared.constants import AuditAction, DriftStatus, EventResult
from tests.fakes import FakeWatcher
from warden.core.docker_watcher import LiveContainer
from web.db.repository import Store
from web.event_log import (
    MASK,
    EventFilter,
    is_secret_key,
    mask_secrets,
    parse_utc,
    render_details,
)
from web.runtime import WardenRuntime


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    web_container: LiveContainer,
    cache_container: LiveContainer,
) -> Iterator[TestClient]:
    watcher = FakeWatcher()
    watcher.containers = [web_container, cache_container]
    monkeypatch.setattr(web_app, "DockerWatcher", lambda settings: watcher)
    with TestClient(web_app.app, raise_server_exceptions=False) as test_client:
        yield test_client


def store_of(client: TestClient) -> Store:
    runtime = client.app.state.warden  # type: ignore[attr-defined]
    assert isinstance(runtime, WardenRuntime)
    store = runtime.store
    assert isinstance(store, Store)
    return store


def seed(store: Store) -> None:
    """Two incidents: a drifted cache that Enforce fixed, and a rogue that was adopted."""
    store.record_drift_event(
        ref="infra-cache-1",
        drift_status=DriftStatus.DRIFTED,
        previous_status=DriftStatus.MATCHED,
        service_name="cache",
        container_name="infra-cache-1",
        trigger="Poller Evaluation",
        reason="Changed: image",
        correlation_id="INC-101",
        details_json={
            "image": "redis:latest",
            "diff": [
                {
                    "field": "image",
                    "expected": "redis:alpine",
                    "observed": "redis:latest",
                    "changed": True,
                },
                {
                    "field": "environment",
                    "expected": "REDIS_PASSWORD=${REDIS_PASSWORD}",
                    "observed": "REDIS_PASSWORD=hunter2",
                    "changed": True,
                },
            ],
        },
    )
    store.record_audit(
        AuditAction.DRIFT_DETECTED, "infra-cache-1", "DRIFT on image", correlation_id="INC-101"
    )
    store.record_audit(
        AuditAction.REVERT_COMPLETED,
        "infra-cache-1",
        "recreate of cache",
        correlation_id="INC-101",
        details_json={"previous_container_id": "a" * 12, "new_container_id": "b" * 12},
    )
    store.record_drift_event(
        ref="rogue-web",
        drift_status=DriftStatus.ROGUE,
        previous_status=None,
        service_name=None,
        container_name="rogue-web",
        trigger="Docker Event",
        reason="Not in baseline (no Compose project labels)",
        correlation_id="INC-102",
    )
    store.record_audit(
        AuditAction.ROGUE_DETECTED, "rogue-web", "SHADOW IT", correlation_id="INC-102"
    )
    store.record_audit(
        AuditAction.ADOPT_PR_OPENED,
        "rogue-web",
        "PR #38 opened: adopt 'rogue-web' as service 'rogue-web'",
        correlation_id="INC-102",
        details_json={"service_name": "rogue-web", "env": {"API_TOKEN": "abc123", "PORT": "80"}},
    )
    store.record_audit(
        AuditAction.REVERSAL_FAILED, "rogue-web", "reverses #5: failed", correlation_id="INC-102"
    )


# --------------------------------------------------------------- units


def test_filter_parsing_ignores_bad_values() -> None:
    f = EventFilter.from_params(
        {"category": "nope", "severity": "HIGH", "since": "2026-11-01", "until": "bad", "q": " x "}
    )
    assert f.category == "all" and f.severity == "high" and f.query == "x"
    assert f.since == datetime(2026, 11, 1, tzinfo=timezone.utc) and f.until is None
    assert f.raw_until == "" and f.params() == {"severity": "high", "since": "2026-11-01", "q": "x"}
    assert EventFilter.from_params({"severity": "urgent"}).severity is None
    end = parse_utc("2026-11-01", end_of_day=True)
    assert end is not None and end.hour == 23 and end.tzinfo is timezone.utc
    assert parse_utc("2026-11-01T10:30") == datetime(2026, 11, 1, 10, 30, tzinfo=timezone.utc)
    assert EventFilter.from_params({"q": "inc-007"}).correlation == "INC-007"
    assert EventFilter.from_params({"q": "INC-007x"}).correlation is None
    assert EventFilter().query_string(category="all") == ""
    assert EventFilter(category="actions").query_string(format="csv") == (
        "?category=actions&format=csv"
    )


def test_secret_masking() -> None:
    assert is_secret_key("MARIADB_ROOT_PASSWORD") and is_secret_key("GITHUB_PAT")
    assert is_secret_key("api_token") and not is_secret_key("PATH")
    assert not is_secret_key("image") and not is_secret_key("service_name")
    masked = mask_secrets(
        {
            "env": ["DB_PASSWORD=s3cret", "PATH=/usr/bin"],
            "API_KEY": "abc",
            "credentials": {"user": "u"},
            "diff": "A=1, SECRET_TOKEN=xyz, B=2",
        }
    )
    assert masked["env"] == [f"DB_PASSWORD={MASK}", "PATH=/usr/bin"]
    assert masked["API_KEY"] == MASK
    assert masked["credentials"] == {"user": "u"}  # nested structures are walked, not blanked
    assert masked["diff"] == f"A=1, SECRET_TOKEN={MASK}, B=2"


def test_render_details_is_generic_escaped_and_masked() -> None:
    html = str(
        render_details(
            {
                "container_name": "<web>",
                "diff": [
                    {"field": "image", "expected": "a", "observed": "b", "changed": True},
                    {"field": "ports", "expected": "1", "observed": "1", "changed": False},
                ],
                "tags": ["x", "y"],
                "password": "hunter2",
            }
        )
    )
    assert "&lt;web&gt;" in html and "<web>" not in html
    assert '<table class="detail-table">' in html and '<tr class="changed">' in html
    assert "<th>Expected</th>" in html and "<dt" in html and "Container name" in html
    assert '<ul class="detail-list">' in html
    assert "hunter2" not in html and MASK in html


# ----------------------------------------------------------------- page


def test_events_page_badges_drawers_and_incident_links(client: TestClient) -> None:
    seed(store_of(client))
    page = client.get("/events").text
    assert ">Severity</th>" in page and ">Result</th>" in page and ">Incident</th>" in page
    assert 'href="/events?q=INC-101"' in page and 'href="/events?q=INC-102"' in page
    assert 'data-expand="audit-' in page and 'class="detail-row" hidden' in page
    assert ">Success</span>" in page and ">Pending</span>" in page and ">Failed</span>" in page
    assert ">Medium</span>" in page and ">High</span>" in page
    assert "Previous container id" in page and "aaaaaaaaaaaa" in page
    assert "hunter2" not in page and "abc123" not in page  # secrets never reach the page
    assert "Export CSV" in page and "/api/export/audit?format=csv" in page
    assert "/api/export/transitions?format=json" in page


def test_incident_filter_lists_related_events_oldest_first(client: TestClient) -> None:
    seed(store_of(client))
    page = client.get("/events?q=inc-102").text
    assert 'id="incident-banner"' in page and "INC-102" in page
    audit_part = page.split('id="audit-table"')[1]
    order = [
        audit_part.index(label) for label in ("Rogue Detected", "PR Opened", "Reversal Failed")
    ]
    assert order == sorted(order)  # chronological
    assert "infra-cache-1" not in audit_part
    assert "row-highlight" in audit_part


def test_filters_narrow_both_tables(client: TestClient) -> None:
    seed(store_of(client))
    page = client.get("/events?severity=high").text
    transitions, audit = page.split('id="audit-table"')
    assert "rogue-web" in transitions and "infra-cache-1" not in transitions.split("<tbody")[1]
    assert (
        "Rogue Detected" in audit and "Reversal Failed" in audit and "Drift Detected" not in audit
    )
    reversals = client.get("/events?category=reversals").text.split('id="audit-table"')[1]
    assert "Reversal Failed" in reversals and "PR Opened" not in reversals
    searched = client.get("/events?q=cache").text.split('id="audit-table"')[1]
    assert "infra-cache-1" in searched and "rogue-web" not in searched
    future = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    empty = client.get(f"/events?since={future}").text
    assert "No transitions match these filters." in empty
    assert "No audit rows match these filters." in empty


# --------------------------------------------------------------- export


def test_export_csv_respects_filters(client: TestClient) -> None:
    seed(store_of(client))
    response = client.get("/api/export/audit?format=csv&q=INC-102&category=actions")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="driftwarden-audit-log-' in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["action"] for r in rows] == ["adopt_pr_opened"]  # actions only, INC-102 only
    row = rows[0]
    assert row["correlation_id"] == "INC-102" and row["result"] == "pending"
    assert row["severity"] == "low" and row["timestamp"].endswith("Z")
    details = json.loads(row["details_json"])
    assert details["service_name"] == "rogue-web" and details["env"]["API_TOKEN"] == MASK


def test_export_json_transitions_keeps_structure(client: TestClient) -> None:
    seed(store_of(client))
    response = client.get("/api/export/transitions?format=json&severity=medium")
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    document = response.json()
    assert document["table"] == "transitions" and document["filters"]["severity"] == "medium"
    assert document["count"] == len(document["rows"]) >= 1
    cache = next(r for r in document["rows"] if r["ref"] == "infra-cache-1")
    assert cache["correlation_id"] == "INC-101" and cache["detected_at"].endswith("Z")
    assert cache["details_json"]["diff"][1]["observed"] == f"REDIS_PASSWORD={MASK}"
    assert all(r["severity"] == "medium" for r in document["rows"])


def test_export_rejects_unknown_table_or_format(client: TestClient) -> None:
    assert client.get("/api/export/whitelist").status_code == 404
    assert client.get("/api/export/audit?format=xml").status_code == 400


def test_store_date_range(client: TestClient) -> None:
    store = store_of(client)
    store.record_audit(AuditAction.SETTINGS_CHANGED, "x", result=EventResult.SUCCESS)
    now = datetime.now(timezone.utc)
    assert store.list_audit(since=now - timedelta(minutes=5))
    assert store.list_audit(since=now + timedelta(minutes=5)) == []
    assert store.list_audit(until=now - timedelta(days=1)) == []
    assert store.list_drift_events(since=now + timedelta(minutes=5)) == []


def test_events_url_is_cleaned_of_empty_and_default_params(client: TestClient) -> None:
    response = client.get("/events?q=&severity=&since=&until=&category=all", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/events"
    response = client.get(
        "/events?category=reversals&q=web&since=&severity=bogus", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/events?category=reversals&q=web"
    clean = client.get("/events?category=reversals&q=web", follow_redirects=False)
    assert clean.status_code == 200  # already clean: no redirect loop


def test_events_page_has_swappable_sections(client: TestClient) -> None:
    page = client.get("/events").text
    for marker in (
        'id="events-filter"',
        'id="incident-banner-slot"',
        'id="transitions-section" data-swap',
        'id="audit-section" data-swap',
        "data-filter-clear hidden",
    ):
        assert marker in page
    filtered = client.get("/events?severity=high").text
    assert "data-filter-clear hidden" not in filtered
