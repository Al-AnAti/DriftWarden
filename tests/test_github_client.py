"""GitHub client behaviour with the REST layer faked out."""

import base64
import json
from typing import Any

import pytest
from github import GithubException

import warden.automation.github_client as github_client_module
from shared.exceptions import AdoptionError, ConfigurationError, GitBaselineFetchError
from warden.automation.github_client import ADOPTION_BRANCH_PREFIX, GitHubClient, slugify
from warden.config import WardenSettings


def github_settings(monkeypatch: pytest.MonkeyPatch) -> WardenSettings:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    monkeypatch.setenv("BASELINE_FILE_PATH", "docker-compose.yml")
    return WardenSettings(_env_file=None)  # type: ignore[call-arg]


class FakeRequester:
    def __init__(self, responses: list[tuple[int, dict[str, Any], str]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def requestJson(  # noqa: N802 - PyGithub's method name
        self, verb: str, url: str, parameters: Any = None, headers: Any = None
    ) -> tuple[int, dict[str, Any], str]:
        self.calls.append({"verb": verb, "url": url, "parameters": parameters, "headers": headers})
        return self.responses.pop(0)


class FakeGithub:
    def __init__(self, requester: FakeRequester) -> None:
        self.requester = requester


def test_slugify() -> None:
    assert slugify("Rogue DB_1!") == "rogue-db-1"
    assert slugify("///") == "container"


def test_requires_configuration(settings: WardenSettings) -> None:
    client = GitHubClient(settings)
    with pytest.raises(ConfigurationError):
        _ = client.repo_name
    with pytest.raises(ConfigurationError):
        client.fetch_baseline()


def test_fetch_baseline_uses_etag_and_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(github_settings(monkeypatch))
    content = base64.b64encode(b"services: {}\n").decode()
    requester = FakeRequester(
        [
            (
                200,
                {"ETag": 'W/"abc"'},
                json.dumps({"content": content, "encoding": "base64", "sha": "deadbeef"}),
            ),
            (304, {}, ""),
            (404, {}, json.dumps({"message": "Not Found"})),
        ]
    )
    client._github = FakeGithub(requester)  # type: ignore[assignment]

    fetched = client.fetch_baseline()
    assert fetched is not None
    assert fetched.content == "services: {}\n"
    assert fetched.sha == "deadbeef"
    assert fetched.etag == 'W/"abc"'
    assert requester.calls[0]["url"] == "/repos/owner/infra/contents/docker-compose.yml"
    assert requester.calls[0]["parameters"] == {"ref": "main"}

    assert client.fetch_baseline(fetched.etag) is None
    assert requester.calls[1]["headers"] == {"If-None-Match": 'W/"abc"'}

    with pytest.raises(GitBaselineFetchError, match="404"):
        client.fetch_baseline()


class FakeContent:
    """Stands in for ``github.ContentFile.ContentFile``."""

    def __init__(self, text: str, sha: str) -> None:
        self.decoded_content = text.encode()
        self.sha = sha


class FakeCommit:
    sha = "base-sha"


class FakeBranch:
    commit = FakeCommit()


class FakeHead:
    def __init__(self, ref: str) -> None:
        self.ref = ref


class FakePull:
    def __init__(self, number: int, head_ref: str) -> None:
        self.number = number
        self.html_url = f"https://github.com/owner/infra/pull/{number}"
        self.head = FakeHead(head_ref)


class FakeRepo:
    def __init__(self, open_pulls: list[FakePull] | None = None, fail_update: bool = False) -> None:
        self.open_pulls = open_pulls or []
        self.fail_update = fail_update
        self.refs: list[tuple[str, str]] = []
        self.updates: list[dict[str, Any]] = []
        self.pulls: list[dict[str, Any]] = []

    def get_pulls(self, **_: Any) -> list[FakePull]:
        return self.open_pulls

    def get_branch(self, _: str) -> FakeBranch:
        return FakeBranch()

    def create_git_ref(self, ref: str, sha: str) -> None:
        self.refs.append((ref, sha))

    def get_contents(self, path: str, ref: str = "") -> FakeContent:
        return FakeContent("services:\n  web:\n    image: nginx\n", "file-sha")

    def update_file(
        self, path: str, message: str, content: str, sha: str, branch: str = ""
    ) -> None:
        if self.fail_update:
            raise GithubException(422, {"message": "nope"}, None)
        self.updates.append(
            {"path": path, "message": message, "content": content, "sha": sha, "branch": branch}
        )

    def create_pull(self, base: str, head: str, title: str = "", body: str = "") -> FakePull:
        self.pulls.append({"base": base, "head": head, "title": title, "body": body})
        return FakePull(42, head)


def client_with_repo(monkeypatch: pytest.MonkeyPatch, repo: FakeRepo) -> GitHubClient:
    monkeypatch.setattr(github_client_module, "ContentFile", FakeContent)
    client = GitHubClient(github_settings(monkeypatch))
    monkeypatch.setattr(client, "_repo", lambda: repo)
    return client


def test_open_adoption_pr_creates_branch_commit_and_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    client = client_with_repo(monkeypatch, repo)

    pull = client.open_adoption_pr(
        container_name="rogue-db",
        service_name="rogue-db",
        mutate=lambda text: text + "  rogue-db:\n    image: mariadb\n",
        title="adopt",
        body="body",
    )
    assert pull.number == 42 and not pull.reused
    assert pull.branch.startswith(f"{ADOPTION_BRANCH_PREFIX}rogue-db-")
    assert repo.refs[0] == (f"refs/heads/{pull.branch}", "base-sha")
    assert repo.updates[0]["sha"] == "file-sha" and repo.updates[0]["branch"] == pull.branch
    assert "mariadb" in repo.updates[0]["content"]
    assert repo.pulls[0] == {"base": "main", "head": pull.branch, "title": "adopt", "body": "body"}


def test_open_adoption_pr_reuses_open_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo(open_pulls=[FakePull(7, f"{ADOPTION_BRANCH_PREFIX}rogue-db-20260101")])
    client = client_with_repo(monkeypatch, repo)
    pull = client.open_adoption_pr(
        container_name="rogue-db", service_name="rogue-db", mutate=lambda t: t, title="t", body="b"
    )
    assert pull.reused and pull.number == 7
    assert repo.refs == [] and repo.pulls == []


def test_open_adoption_pr_wraps_github_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = client_with_repo(monkeypatch, FakeRepo(fail_update=True))
    with pytest.raises(AdoptionError, match="422"):
        client.open_adoption_pr(
            container_name="x", service_name="x", mutate=lambda t: t, title="t", body="b"
        )
