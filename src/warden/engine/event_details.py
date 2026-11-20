"""Structured, secret-free metadata for Events Log rows (``details_json``).

Every audit row and drift transition the reconciler writes can carry a JSON
mapping that the Events Log renders as an expandable key/value drawer. This
module builds those mappings in one place so the keys stay consistent across
detections, Enforce rows and transitions, and so that no environment value
ever reaches the database: environment entries are reduced to ``KEY=***``
(``${KEY}`` placeholders and ``<unset>`` markers stay readable because they
carry no value).
"""

import re
from typing import Any

from shared.models import BaselineInfo, DriftRecord, FieldDiff
from warden.engine.diff_engine import UNSET, normalise_image
from warden.engine.verification import Verification

MASK = "***"
"""Replacement for every environment value and every secret-looking label value."""

MASKED_FIELDS: frozenset[str] = frozenset({"environment"})
"""Diff fields whose values are always masked."""

_SECRET_KEY = re.compile(r"(pass(word)?|secret|token|api[_-]?key|credential|auth|private)", re.I)
"""Keys of other ``KEY=value`` fields (labels) whose value is masked too."""

_PAIR_SEPARATOR = ", "


def mask_pairs(text: str | None, *, mask_all: bool) -> str | None:
    """Mask the values of a ``KEY=value, KEY2=value2`` diff string.

    Args:
        text: The diff engine's rendering of a mapping field, or ``None``.
        mask_all: Mask every value (environment) instead of only secret-looking keys.

    Returns:
        The same string with values replaced by :data:`MASK`; placeholders of the
        form ``${KEY}`` and the ``<unset>`` marker are left as they are.
    """
    if text is None or text == "(none)":
        return text
    masked: list[str] = []
    for pair in text.split(_PAIR_SEPARATOR):
        key, sep, value = pair.partition("=")
        if not sep:
            masked.append(pair)
            continue
        placeholder = value == UNSET or (value.startswith("${") and value.endswith("}"))
        if placeholder or not (mask_all or _SECRET_KEY.search(key)):
            masked.append(pair)
        else:
            masked.append(f"{key}={MASK}")
    return _PAIR_SEPARATOR.join(masked)


def mask_diff(diff: FieldDiff) -> dict[str, Any]:
    """One compared field as a JSON row with its values masked where needed."""
    mask_all = diff.field in MASKED_FIELDS
    maskable = mask_all or diff.field == "labels"
    return {
        "field": diff.field,
        "expected": mask_pairs(diff.expected, mask_all=mask_all) if maskable else diff.expected,
        "observed": mask_pairs(diff.observed, mask_all=mask_all) if maskable else diff.observed,
        "changed": diff.changed,
    }


def diff_details(diffs: list[FieldDiff], *, changed_only: bool = True) -> list[dict[str, Any]]:
    """Expected-versus-actual rows for ``details_json``."""
    return [mask_diff(d) for d in diffs if d.changed or not changed_only]


def image_details(image: str | None) -> dict[str, Any]:
    """``image``, ``image_repository`` and ``image_tag`` of an image reference."""
    if not image:
        return {}
    canonical = normalise_image(image)
    if canonical.startswith("sha256:") or "@" in canonical:
        return {"image": image}
    repository, _, tag = canonical.rpartition(":")
    return {"image": image, "image_repository": repository, "image_tag": tag}


def baseline_details(baseline: BaselineInfo) -> dict[str, Any]:
    """Where the baseline came from: source, Git repository, branch, path and SHA."""
    details: dict[str, Any] = {"baseline_source": baseline.source.value}
    if baseline.repo:
        details["git_repo"] = baseline.repo
    if baseline.branch:
        details["git_branch"] = baseline.branch
    if baseline.path:
        details["baseline_path"] = baseline.path
    if baseline.sha:
        details["git_sha"] = baseline.sha
    return details


def _identity(record: DriftRecord) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if record.service_name:
        details["service_name"] = record.service_name
    if record.container_name:
        details["container_name"] = record.container_name
    if record.container_id:
        details["container_id"] = record.container_id
    if record.project:
        details["compose_project"] = record.project
    return details


def record_details(
    record: DriftRecord, baseline: BaselineInfo, trigger: str | None = None
) -> dict[str, Any]:
    """Metadata of a detection or transition: identity, image, Git source and diff.

    Args:
        record: The dashboard entry as evaluated.
        baseline: The baseline it was compared with.
        trigger: What caused the evaluation (``Poller Evaluation`` ...).

    Returns:
        A JSON-ready mapping; the diff lists only changed fields, values masked.
    """
    details = _identity(record)
    details.update(image_details(record.image))
    details.update(baseline_details(baseline))
    details["status"] = record.drift_status.value
    details["container_state"] = record.status
    if trigger:
        details["trigger_source"] = trigger
    if record.reason:
        details["reason"] = record.reason
    changed = diff_details(record.diffs)
    if changed:
        details["diff"] = changed
    return details


def enforce_details(
    record: DriftRecord,
    baseline: BaselineInfo,
    *,
    source: str,
    service_name: str | None = None,
    target_name: str | None = None,
    image: str | None = None,
) -> dict[str, Any]:
    """Metadata written with an Enforce recreate row before its outcome is known.

    Args:
        record: The entry Enforce acted on (its container is the *previous* one).
        baseline: The baseline enforced.
        source: ``autonomous enforcement``, ``manual Enforce`` or ``batch Enforce``.
        service_name: Baseline service recreated.
        target_name: Name of the container being created.
        image: Image of the container being created.

    Returns:
        A JSON-ready mapping with ``previous_container_id`` set and
        ``new_container_id`` still absent.
    """
    details: dict[str, Any] = {"trigger_source": source, "action": "recreate"}
    if service_name or record.service_name:
        details["service_name"] = service_name or record.service_name
    if target_name or record.container_name:
        details["container_name"] = target_name or record.container_name
    if record.container_id:
        details["previous_container_id"] = record.container_id
    if record.container_name and target_name and record.container_name != target_name:
        details["previous_container_name"] = record.container_name
    details.update(image_details(image or record.image))
    if record.image and image and normalise_image(record.image) != normalise_image(image):
        details["previous_image"] = record.image
    details.update(baseline_details(baseline))
    details["previous_status"] = record.drift_status.value
    if record.reason:
        details["reason"] = record.reason
    changed = diff_details(record.diffs)
    if changed:
        details["diff"] = changed
    return details


def removal_details(record: DriftRecord, baseline: BaselineInfo, *, source: str) -> dict[str, Any]:
    """Metadata written with an Enforce removal row."""
    details = enforce_details(record, baseline, source=source)
    details["action"] = "remove"
    details.pop("diff", None)
    return details


def verification_details(verification: Verification) -> dict[str, Any]:
    """What verification found, to merge into the Enforce row once it is known."""
    details: dict[str, Any] = {
        "verified": verification.ok,
        "verification": verification.reason,
    }
    container = verification.container
    if container is not None:
        details["container_state"] = container.state
    if not verification.ok and verification.changed_fields:
        details["verification_diff"] = diff_details(verification.diffs)
    return details
