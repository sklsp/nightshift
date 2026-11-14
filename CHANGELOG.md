# Changelog

## 0.2.0

- render: spec files reference the run dir and repo with absolute paths
- render: tick lines in item specs use the configured prefix instead of T
- supervise: launch uses an absolute run dir path and a grace period before counting a session as empty
- repo: .gitattributes keeps checkouts on Windows at LF and .gitignore now lists .coverage
- cli: new doctor command that checks run.toml, items.md, the repo git work tree, required programs and timing hints
- gate: new gate clean removes the .gate folder; stale runs (log older than 120 s without a result) are reported instead of blocking forever
- cli: status --json prints the summary as one JSON object with keys name, total, done, blocked, next_item, next_title, gate and commits
- release: version 0.2.0 and this changelog

## 0.1.0

- init: create a run directory with example run.toml and items.md
- build: render 00-start.md, one spec file per item and checklist.txt from config plus items
- textlint: flag em dashes, en dashes and trailing whitespace in rendered files so specs stay dash-free
- progress: read PROGRESS files to track done, blocked and the next item
- gate start/wait/file: run checks detached so every call fits a 30 second command limit
- agent adapters: launch and status commands for local agents
- supervise: relaunch the agent when a session ends, with a restart limit
- status: one-screen summary of progress, last gate line and recent commits
- docs: README quickstart, config reference and docs/LESSONS.md
- example: bundled hello run used by the tests
- CI: GitHub Actions matrix (ubuntu and windows, Python 3.11 to 3.13) running ruff and pytest
