"""Check that a run's history has exactly one well-formed commit per item."""

import re
from dataclasses import dataclass
from pathlib import Path

from nightshift.config import RunConfig
from nightshift.git import current_branch, run_git
from nightshift.items import Item


class CommitError(ValueError):
    """git could not be run or the commit range could not be resolved."""


@dataclass(frozen=True)
class Commit:
    """One git commit in the checked range, with its hash, subject and body."""

    hash: str
    subject: str
    body: str


_FALLBACK_BASES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")


def _merge_base(repo: Path, ref: str) -> str | None:
    """Return merge-base(ref, HEAD), or None when git cannot compute it."""
    result = run_git(repo, "merge-base", ref, "HEAD")
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def resolve_base(config: RunConfig) -> str | None:
    """Return the default range base for one run.

    When [run] base is set, merge-base(base, HEAD); else when branch is set and differs from the
    current branch, merge-base(branch, HEAD); else the first of origin/HEAD, origin/main,
    origin/master, main, master that exists and is not HEAD itself; else the first commit.
    """
    repo = config.repo
    if config.base:
        base = _merge_base(repo, config.base)
        if base is not None:
            return base
    elif config.branch and config.branch != current_branch(repo):
        base = _merge_base(repo, config.branch)
        if base is not None:
            return base
    head_result = run_git(repo, "rev-parse", "HEAD")
    head = head_result.stdout.strip() if head_result.returncode == 0 else ""
    for candidate in _FALLBACK_BASES:
        result = run_git(repo, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        if result.returncode != 0 or not result.stdout.strip():
            continue
        # where this branch left the candidate, not the candidate's tip: a run branch off another branch
        # (v1, say) must not count every commit the candidate does not have
        base = _merge_base(repo, candidate)
        if base and base != head:
            return base
    root = run_git(config.repo, "rev-list", "--max-parents=0", "HEAD")
    if root.returncode != 0 or not root.stdout.strip():
        return None
    return root.stdout.splitlines()[0].strip()


def read_commits(repo: Path, base: str) -> list[Commit]:
    """Return the commits in git log <base>..HEAD, newest first."""
    result = run_git(repo, "log", f"{base}..HEAD", "--pretty=format:%x01%H%x02%s%x03%b")
    if result.returncode != 0:
        raise CommitError(f"git log failed: {result.stderr.strip()}")
    commits: list[Commit] = []
    for chunk in result.stdout.split("\x01"):
        if not chunk:
            continue
        head, _, body = chunk.partition("\x03")
        commit_hash, _, subject = head.partition("\x02")
        commits.append(Commit(hash=commit_hash.strip(), subject=subject.strip(), body=body))
    return commits


def changed_files(repo: Path, commit_hash: str) -> list[str]:
    """Return the repo-relative posix paths of the files one commit changed."""
    result = run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash)
    if result.returncode != 0:
        raise CommitError(f"git diff-tree failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def commit_report(config: RunConfig, items: list[Item], base: str | None = None) -> list[str]:
    """Return one line per problem with the commits since base; empty when clean."""
    if base is None:
        base = resolve_base(config)
    if base is None:
        return []
    commits = read_commits(config.repo, base)

    pattern = re.compile(rf"^{re.escape(config.prefix)}(\d+): ")
    bad_subjects: list[str] = []
    empty_bodies: list[str] = []
    protected_lines: list[str] = []
    by_item: dict[int, list[Commit]] = {}
    for commit in commits:
        short = commit.hash[:7]
        match = pattern.match(commit.subject)
        if match is None:
            bad_subjects.append(f"{short} bad subject: '{commit.subject}' (expected '{config.prefix}<n>: ...')")
        else:
            by_item.setdefault(int(match.group(1)), []).append(commit)
        if not commit.body.strip():
            empty_bodies.append(f"{short} has an empty body")
        if config.protect:
            for path in changed_files(config.repo, commit.hash):
                if any(path.startswith(prefix) for prefix in config.protect):
                    protected_lines.append(f"{short} touches protected {path}")

    lines = list(bad_subjects)
    for number in sorted(by_item):
        group = by_item[number]
        if len(group) > 1:
            shorts = ", ".join(commit.hash[:7] for commit in group)
            lines.append(f"{config.prefix}{number} has {len(group)} commits ({shorts})")
    committed_numbers = set(by_item)
    for item in items:
        if item.number not in committed_numbers:
            lines.append(f"{config.prefix}{item.number} has no commit")
    lines.extend(empty_bodies)
    lines.extend(protected_lines)
    return lines
