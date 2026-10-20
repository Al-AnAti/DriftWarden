"""Stateless GitHub REST access for baseline fetching and Reverse-GitOps PRs.

Every Git operation goes through PyGithub; no local clone or subprocess git is
ever used. Baseline polls carry the previous ``ETag`` so unchanged files come
back as ``304 Not Modified`` and cost no API quota.
"""

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from github import Auth, Github, GithubException
from github.ContentFile import ContentFile
from github.Repository import Repository
from loguru import logger

from shared.exceptions import AdoptionError, ConfigurationError, GitBaselineFetchError
from warden.config import WardenSettings

ADOPTION_BRANCH_PREFIX = "warden/adopt-"
"""Prefix shared by every branch DriftWarden pushes for an adoption."""


@dataclass(slots=True, frozen=True)
class FetchedBaseline:
    """Content of the baseline file as served by GitHub.

    Attributes:
        content: Decoded file text.
        sha: Git blob SHA of the file.
        etag: ``ETag`` header to send on the next poll.
    """

    content: str
    sha: str
    etag: str | None


@dataclass(slots=True, frozen=True)
class AdoptionPullRequest:
    """A pull request opened (or found) for an adoption.

    Attributes:
        number: Pull request number.
        url: Browser URL of the pull request.
        branch: Head branch name.
        reused: Whether an already-open pull request was returned instead of a new one.
    """

    number: int
    url: str
    branch: str
    reused: bool = False


def slugify(text: str) -> str:
    """Turn a container or service name into a branch-safe slug.

    Args:
        text: Arbitrary name.

    Returns:
        Lowercase text with runs of unsafe characters collapsed to ``-``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "container"


class GitHubClient:
    """Thin wrapper around PyGithub scoped to the configured target repository.

    Args:
        settings: Validated application settings.
    """

    def __init__(self, settings: WardenSettings) -> None:
        self._settings = settings
        self._github: Github | None = None

    @property
    def repo_name(self) -> str:
        """``owner/name`` of the target repository.

        Raises:
            ConfigurationError: If ``TARGET_REPO`` is unset.
        """
        if not self._settings.target_repo:
            raise ConfigurationError("TARGET_REPO is not configured")
        return self._settings.target_repo

    @property
    def branch(self) -> str:
        """Branch holding the baseline.

        Raises:
            ConfigurationError: If ``TARGET_BRANCH`` is unset.
        """
        if not self._settings.target_branch:
            raise ConfigurationError("TARGET_BRANCH is not configured")
        return self._settings.target_branch

    @property
    def path(self) -> str:
        """Repository-relative path of the baseline file."""
        return self._settings.baseline_file_path

    def _client(self) -> Github:
        """Lazily build the authenticated PyGithub client.

        Raises:
            ConfigurationError: If ``GITHUB_PAT`` is unset.
        """
        if self._github is None:
            if self._settings.github_pat is None:
                raise ConfigurationError("GITHUB_PAT is not configured")
            token = self._settings.github_pat.get_secret_value()
            self._github = Github(auth=Auth.Token(token), per_page=50)
        return self._github

    def _repo(self) -> Repository:
        """Resolve the target repository object."""
        try:
            return self._client().get_repo(self.repo_name)
        except GithubException as exc:
            raise GitBaselineFetchError(
                f"Cannot access repository {self.repo_name}: {_describe(exc)}"
            ) from exc

    def verify_access(self) -> str:
        """Check that the token works and can see the target repository.

        Returns:
            The authenticated login.

        Raises:
            GitBaselineFetchError: If the token is rejected or the repo is unreachable.
        """
        try:
            login = str(self._client().get_user().login)
            self._repo()
        except GithubException as exc:
            raise GitBaselineFetchError(f"GitHub token rejected: {_describe(exc)}") from exc
        except GitBaselineFetchError:
            raise
        except Exception as exc:  # network failures surface as requests errors
            raise GitBaselineFetchError(f"GitHub unreachable: {exc}") from exc
        logger.info("GitHub access verified as {} on {}", login, self.repo_name)
        return login

    def fetch_baseline(self, etag: str | None = None) -> FetchedBaseline | None:
        """Fetch the baseline file, honouring the cached ``ETag``.

        Args:
            etag: ``ETag`` from the previous successful fetch, if any.

        Returns:
            The fetched file, or ``None`` when GitHub answered ``304 Not Modified``.

        Raises:
            GitBaselineFetchError: On any HTTP error or malformed payload.
        """
        url = f"/repos/{self.repo_name}/contents/{quote(self.path)}"
        headers: dict[str, Any] = {}
        if etag:
            headers["If-None-Match"] = etag
        try:
            status, response_headers, body = self._client().requester.requestJson(
                "GET", url, parameters={"ref": self.branch}, headers=headers
            )
        except GithubException as exc:
            raise GitBaselineFetchError(f"Baseline fetch failed: {_describe(exc)}") from exc
        except Exception as exc:
            raise GitBaselineFetchError(f"Baseline fetch failed: {exc}") from exc

        if status == 304:
            logger.debug("Baseline unchanged (304) for {}@{}", self.path, self.branch)
            return None
        if status >= 400:
            raise GitBaselineFetchError(
                f"GitHub returned {status} for {self.repo_name}/{self.path}@{self.branch}: "
                f"{_message_from_body(body)}"
            )
        try:
            payload = json.loads(body)
            encoded = str(payload["content"])
            content = base64.b64decode(encoded).decode("utf-8")
            sha = str(payload["sha"])
        except (KeyError, ValueError, TypeError) as exc:
            raise GitBaselineFetchError(f"Unexpected contents payload: {exc}") from exc
        new_etag = next(
            (str(v) for k, v in response_headers.items() if str(k).lower() == "etag"), None
        )
        logger.info("Fetched baseline {}@{} sha={}", self.path, self.branch, sha[:12])
        return FetchedBaseline(content=content, sha=sha, etag=new_etag)

    def find_open_adoption_pr(self, container_name: str) -> AdoptionPullRequest | None:
        """Find an open adoption pull request for a container, if one exists.

        Args:
            container_name: Name of the adopted container.

        Returns:
            The open pull request, or ``None``.
        """
        prefix = f"{ADOPTION_BRANCH_PREFIX}{slugify(container_name)}-"
        try:
            for pull in self._repo().get_pulls(state="open", base=self.branch):
                if str(pull.head.ref).startswith(prefix):
                    return AdoptionPullRequest(
                        number=int(pull.number),
                        url=str(pull.html_url),
                        branch=str(pull.head.ref),
                        reused=True,
                    )
        except GithubException as exc:
            logger.warning("Could not list open pull requests: {}", _describe(exc))
        return None

    def open_adoption_pr(
        self,
        *,
        container_name: str,
        service_name: str,
        mutate: Callable[[str], str],
        title: str,
        body: str,
    ) -> AdoptionPullRequest:
        """Create a branch, commit the mutated baseline and open a pull request.

        The current file content is re-read from the base branch right before the
        mutation so the commit always applies on top of the latest baseline.

        Args:
            container_name: Name of the container being adopted (branch slug).
            service_name: Compose service name written to the baseline.
            mutate: Callback that receives the latest file text and returns the new text.
            title: Pull request title.
            body: Pull request body (Markdown).

        Returns:
            The opened pull request, or an already-open one for the same container.

        Raises:
            AdoptionError: If any GitHub call fails.
        """
        existing = self.find_open_adoption_pr(container_name)
        if existing is not None:
            logger.info("Reusing open adoption PR #{} for {}", existing.number, container_name)
            return existing

        repo = self._repo()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        branch = f"{ADOPTION_BRANCH_PREFIX}{slugify(container_name)}-{stamp}"
        try:
            base_sha = str(repo.get_branch(self.branch).commit.sha)
            repo.create_git_ref(f"refs/heads/{branch}", base_sha)
            current = repo.get_contents(self.path, ref=branch)
            if not isinstance(current, ContentFile):
                raise AdoptionError(f"{self.path} is a directory, not a file")
            latest_text = current.decoded_content.decode("utf-8")
            new_text = mutate(latest_text)
            repo.update_file(
                self.path,
                f"feat(warden): adopt {service_name} into {self.path}",
                new_text,
                str(current.sha),
                branch=branch,
            )
            pull = repo.create_pull(base=self.branch, head=branch, title=title, body=body)
        except AdoptionError:
            raise
        except GithubException as exc:
            raise AdoptionError(f"GitHub rejected the adoption: {_describe(exc)}") from exc
        except Exception as exc:
            raise AdoptionError(f"Adoption failed: {exc}") from exc
        logger.info("Opened adoption PR #{} ({}) on branch {}", pull.number, pull.html_url, branch)
        return AdoptionPullRequest(
            number=int(pull.number), url=str(pull.html_url), branch=branch, reused=False
        )


def _describe(exc: GithubException) -> str:
    """Render a PyGithub exception as a short message."""
    data = exc.data if isinstance(exc.data, dict) else {}
    message = str(data.get("message", "")) if data else ""
    return f"{exc.status} {message}".strip()


def _message_from_body(body: str) -> str:
    """Pull the ``message`` field out of an error payload, if any."""
    try:
        payload = json.loads(body)
    except ValueError:
        return body[:200]
    if isinstance(payload, dict):
        return str(payload.get("message", body[:200]))
    return body[:200]
