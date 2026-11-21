# Changelog

## Unreleased

- typing: mypy strict now covers tests/ too ([tool.mypy] files = ["src", "tests"]); every test function, fake and fixture is annotated, and setuptools.* ignores missing stubs
- lint: ruff select gains PTH and RUF; every finding is fixed by hand with behaviour unchanged (Path methods for os calls, raw match= patterns with escaped metacharacters, underscore-prefixed unused unpacks)
- lint: ruff select gains RET and PERF; the seven findings are fixed by hand with behaviour unchanged, one loop keeps a noqa where it reads better
- template: start spec gains a never-push rule next to the never-kill-by-name one (no git push, no new remote branches); docs/LESSONS.md gets a Never push section with what happens when an agent pushes first
- tests: supervise gains coverage that a frozen repo is reported stuck even when agent.item_minutes is set, including which limit fires first and that an unreadable fingerprint does not reset the idle clock
- supervise: when the fingerprint changes after at least ten quiet minutes, the log says what changed (HEAD moved or up to three new and gone porcelain lines), so a quiet stretch followed by activity stays readable in the run log

## [0.21.0] - 2026-11-20

- supervise: agent.item_minutes caps how long one item may stay next; when it passes without a commit, the supervisor appends BLOCKED to its progress line and stops the agent once so the run moves on

## [0.20.0] - 2026-11-20

- finish: new command drops PROGRESS-<name>.md once every item is done or blocked and the supervisor has exited, refusing while another process holds the supervisor lock or any item is still open; README quick start and docs/LESSONS.md point at it
- init: --base sets base in the copied run.toml with --from, replacing an existing base line or inserting one after branch (or name) when there is none

## [0.19.0] - 2026-11-19

- supervise: records each item commit (short hash and unix time) as an event of its own in the supervisor log, so a run remembers its commits even when git no longer shows them as a separate range
- report and history: fall back to the recorded commits when the branch is already merged, so per-item times and the run time line still show after the merge
- README: one sentence in the report section that a supervised run remembers its item commits, so report and history still work after the branch is merged
- release: version 0.19.0 and this changelog

## [0.18.0] - 2026-11-19

- history: new command prints one row per run dir plus a total line across several runs, reusing report's numbers for items, run time, sessions and warnings; missing dirs are noted and skipped
- README: Track record section with the total line from eleven real runs on another project (Iris)
- release: version 0.18.0 and this changelog

## [0.17.2] - 2026-11-19

- template: start spec gains a rule that never kills processes by name (Stop-Process -Name python, taskkill /IM python.exe and pkill python all kill the supervisor and watch too); stop only what you started, by the pid you saved
- lessons: new "Never kill by name" section with what happened in the Iris run and the rule
- release: version 0.17.2 and this changelog

## [0.17.1] - 2026-11-19

- report: adds a Review section between run time and Supervisor with every warning review would print, prefixed by the item id; when there are none it says `- no warnings`, so a reader knows the check ran
- release: version 0.17.1 and this changelog

## [0.17.0] - 2026-11-19

- review: new command prints what each item commit changed: files with added and removed lines, test counts before and after the commit, and warning lines under each block (protected paths touched, fewer tests than before, more than 15 files); warnings never change the exit code
- report: shows the run's branch when it exists in the repo and falls back to HEAD otherwise
- commits: --base accepts any ref that resolves to a commit, not just branches
- release: version 0.17.0 and this changelog

## 0.16.1

- render: when [run] branch is unset and the repo folder does not exist, the start spec names main without calling git
- tests: supervise unit tests that are not about git pass fake git helpers instead of spawning about 66 real git calls per run
- release: version 0.16.1 and this changelog

## 0.16.0

- gate: start takes extra arguments for the test command after a literal -- (for example --durations=25); they apply to this one gate only and the rendered start spec shows the form
- report: reads progress through the .gate copy when cleanup removed the repo file, says 'progress file missing' instead of 0/0 when neither exists, and limits commits to the run's range so a later run reusing the prefix never shows up
- commits: new [run] base key names the range base as a branch or commit; when unset, merge-base with run.branch if it differs from the current branch, else main or master (or their origin copies) when present and not HEAD itself, else the first commit; documented in init's example and the README table
- release: version 0.16.0 and this changelog; all three fixes came from real Iris runs (findings 3 to 5)

## 0.15.0

- config: [run] protect lists path prefixes the agent must never touch; a non-list or a list with a non-string element is rejected at load time
- commits: each commit that touches a file under one of those prefixes gets an extra line naming the short hash and the protected path, in commit order
- render: when protect is set, the start spec gains a rule line listing the paths in backticks; ARCHITECTURE notes it
- init: writes the protect key commented out with an example in the sample run.toml; README's config table lists it
- release: version 0.15.0 and this changelog

## 0.14.0

- init: new --from copies an old run's run.toml and items.md into a fresh run dir, rewriting only the [run] name and branch lines when their flags are given (comments and every other line stay byte for byte) and re-pointing a relative templates path at the same folder from the new dir; without --name or --branch the copy is unchanged, and items.md starts as a skeleton with one empty section
- README: Quick start shows init --from in one line next to plain init and explains what it copies
- release: version 0.14.0 and this changelog

## 0.13.0

- tests: new test_end_to_end.py drives a real supervisor against the fake agent as a subprocess: it launches the agent, watches it commit three items in order and exits with code 0 when every item is ticked; README notes it under the architecture link
- supervise: an agent that finishes before any poll sees it running now ends its session normally instead of being counted as a failed start; found by the new end to end test
- release: version 0.13.0 and this changelog

## 0.12.3

- tests: new test_packaging.py builds a wheel of the repo through the PEP 517 hook without the network and checks it holds both templates, every module and an entry_points.txt with nightshift = nightshift.cli:main; setuptools joins the dev extras and .gitignore lists build/
- README: Install covers pip install from the git repository URL plus the editable dev install with [dev], and Quick start is at most eight commands (init, edit the two files, doctor, build, supervise, watch, report); a new test parses every quick start command against build_parser() so it stays real
- release: version 0.12.3 and this changelog

## 0.12.2

- docs: new docs/ARCHITECTURE.md explains how nightshift works inside from the code: the life of a run with a mermaid sequence diagram, one line per module, the supervisor's rules and why each exists, and the files a run leaves behind; README links it near the top
- release: version 0.12.2 and this changelog

## 0.12.1

- watch: when an item finishes, watch polls one last time before ending, so a commit made after progress finished (the final tick) is printed before the finished line
- supervise: new [agent] launch_ok_codes lists exit codes of the launch command that are not worth a log line; Cline exits with code 1 even when the session starts fine, and every other code is logged as before
- release: version 0.12.1 and this changelog; both fixes came from the first runs nightshift drove alone (two Iris runs on 2026-11-16)

## 0.12.0

- templates: a project can bring its own start.md and item.md; set templates in [run] to a directory relative to the run dir or an absolute path, any file you do not provide falls back to the built-in one, and an unknown placeholder raises a RenderError naming the template file and the placeholder
- doctor: reports a templates line when the key is set, rendering both templates into a temporary directory with the run's real items so a bad placeholder fails before build; a missing directory is reported too
- init: writes the templates key commented out in the example run.toml
- README: Your own templates section lists every placeholder of start.md and item.md
- release: version 0.12.0 and this changelog

## 0.11.0

- watch: new nightshift watch command prints new commits and supervisor events as they appear, ends with a finished line when the progress file is fully ticked and returns 0 on Ctrl+C; --once does one poll that prints what exists now (all events plus the last five commits)
- release: version 0.11.0 and this changelog

## 0.10.0

- report: ends with a Supervisor section holding the last five events from .gate/supervise.jsonl when it has entries, next to the per-item commit times and run time line
- status: shows how long the agent has been on its current item (on N2 for 2 h 38 min), counting up from the newest commit of a done item or, when there is none, from the first supervisor event; --json carries whole minutes under current_minutes
- release: version 0.10.0 and this changelog

## 0.9.1

- typing: mypy strict is on in pyproject.toml and CI runs mypy src after ruff; the injectable callables of supervise carry precise Callable types
- git: new nightshift.git module (run_git, current_branch, git_dir, fingerprint) replaces the duplicated git calls in supervise, cli, render and commits
- release: version 0.9.1 and this changelog; no behaviour change

## 0.9.0

- supervise: takes a per-repo lock in the git directory before it starts and refuses with exit code 2 while another process holds it, so only one supervisor can work on a repo at a time
- config: run.branch now defaults to empty; when set, supervise and doctor refuse to start unless the repo is on that branch, and commits resolves its base against it instead of main
- queue: new nightshift queue command supervises several run dirs back to back in order; it checks them all up front (a missing dir or one without run.toml stops with exit code 2), prints one finished line per run and returns the first non-zero code
- release: version 0.9.0 and this changelog

## 0.8.0

- supervise: appends one JSON object per poll event to .gate/supervise.jsonl, so a finished run leaves an audit trail of what the supervisor saw
- status: shows a Supervisor: section with the last five events from that log when it has entries, and --json carries them under an events key
- report: shows how long each item took from its commit times and a run time line from first to last item commit
- release: version 0.8.0 and this changelog

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
