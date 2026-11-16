# Changelog

## 0.7.0

- agent: a failed status check is unknown, not stopped (is_running returns None instead of False when the command fails or times out)
- supervise: an unreadable status never ends a session; unknown polls are retried up to [agent] status_retry_polls before the session is treated as ended, and recovery is logged
- supervise: a failed agent launch is retried after a backoff that doubles each time (capped at 15 minutes) and never counts as an empty session; [agent] max_launch_failures consecutive failures end the run with exit code 2
- release: version 0.7.0 and this changelog; all three supervisor changes came from the second dogfood run (Sparton run 13), where Windows refused to start new processes for a while

## 0.6.0

- gate: wait exits with code 1 when the check fails and writes the result line to .gate/result.txt so a failed run is visible in scripts and logs
- render: item specs carry a WHEN DONE block (run the check until both pass, tick the item, commit) instead of a bare checklist line
- supervise: copies the progress file into .gate on every poll and reads that copy when cleanup removed the repo file, so a finished run stays finished
- release: version 0.6.0 and this changelog; all three fixes came from the first dogfood run (nightshift6)

## 0.5.0

- config: [run] command names the program the rendered spec tells the agent to run; every gate hint uses the same prefix, stored in .gate/ at start so wait prints what it was started with
- release: version 0.5.0 and this changelog

## 0.4.0

- gate: [check] pre runs any number of extra commands between lint and tests; the first one that fails stops the start
- supervise: fingerprints HEAD plus git status on every poll, reports a stuck agent when nothing changes for idle_minutes and runs [agent] stop to end the session
- cli: new commits command checks that each item has exactly one well-formed commit
- render: two more rules in the start file (keep the why; notes files are append-only)
- release: version 0.4.0 and this changelog

## 0.3.0

- cli: doctor names every check it runs and prints one ok or problem line per check
- cli: new report command that prints a markdown summary of a run for a pull request body, with the commit hash behind each item, the last gate line and --out to write to a file
- report: appends ## Bugs found and ## Ideas sections from BUGS.md and IDEAS.md in the run dir when they hold lines
- release: version 0.3.0 and this changelog

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
