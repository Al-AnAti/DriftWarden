"""Host identity cascade: env, mounted hostname, Docker daemon name, platform."""

from pathlib import Path
from typing import Any

import pytest

from warden.config import WardenSettings
from warden.core.host_identity import resolve_host_identity


def test_env_wins(tmp_path: Path) -> None:
    mounted = tmp_path / "host_hostname"
    mounted.write_text("mounted-host\n", encoding="utf-8")
    name, source = resolve_host_identity(
        "Host-Prod-01", lambda: {"Name": "daemon-host"}, mounted_file=mounted
    )
    assert (name, source) == ("Host-Prod-01", "env")


def test_mounted_file_beats_daemon(tmp_path: Path) -> None:
    mounted = tmp_path / "host_hostname"
    mounted.write_text("  mounted-host \n", encoding="utf-8")
    name, source = resolve_host_identity(
        None, lambda: {"Name": "daemon-host"}, mounted_file=mounted
    )
    assert (name, source) == ("mounted-host", "mount")


def test_daemon_name_when_nothing_is_mounted(tmp_path: Path) -> None:
    name, source = resolve_host_identity(
        "   ", lambda: {"Name": "docker-desktop"}, mounted_file=tmp_path / "absent"
    )
    assert (name, source) == ("docker-desktop", "docker")


def test_platform_fallback_when_daemon_fails(tmp_path: Path) -> None:
    def failing() -> dict[str, Any]:
        raise RuntimeError("daemon down")

    name, source = resolve_host_identity(None, failing, mounted_file=tmp_path / "absent")
    assert source == "platform" and name
    name2, source2 = resolve_host_identity(None, None, mounted_file=tmp_path / "absent")
    assert source2 == "platform" and name2 == name


def test_settings_accept_both_variable_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WARDEN_HOSTNAME", raising=False)
    monkeypatch.setenv("WARDEN_HOST_NAME", "edge-42")
    assert WardenSettings(_env_file=None).hostname == "edge-42"  # type: ignore[call-arg]
    monkeypatch.delenv("WARDEN_HOST_NAME")
    monkeypatch.setenv("WARDEN_HOSTNAME", "edge-43")
    assert WardenSettings(_env_file=None).hostname == "edge-43"  # type: ignore[call-arg]
