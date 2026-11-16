# Lessons from overnight runs

Rules learned the hard way on real unattended runs. Each one cost time when it was broken; nightshift enforces what it can, the rest are for you and your agent.

## One item per file
Agents read ahead: given a list of ten items they start planning item four while doing item one, and drift. One spec file per item, opened only when its turn comes, keeps the working set to exactly what is being done now. The render step exists so you never hand-write these files.

## The progress file is the single source of truth
PROGRESS-<name>.md in the repo root is the only record of what is done; it is committed with each item, so a crashed session resumes from git state instead of chat history. Each tick carries the last pytest line, which lands proof and commit together.

## The 30 second command limit and the detached gate
Agent terminals kill commands that run longer than about 30 seconds, but a real test suite takes minutes. The gate starts the check in a detached process and writes its output to files; each wait call returns within the limit and reads those files. If your direct check does not fit the limit, the tests are too slow: fix them instead of raising the limit.

## Change the command number on every poll
Some agents cancel a session that repeats one tool call five times in a row. `gate wait` takes a call number (1, 2, 3, ...) so no two consecutive commands are identical; the number changes on purpose and must keep changing.

## Never -q twice
pytest hides its summary line when run with -qq, and the gate needs that line to count passed tests. A gated run without a summary line fails even when every test passes, which cost one whole night once. One -q is fine; two is not.

## --cov, not --cov=<module>
Pointing pytest-cov at a module imports it before the tests do, which broke a SQLAlchemy C extension in a real run. The gate therefore runs plain `--cov` and finds the module's line in the report afterwards.

## Log real bugs, do not fix them
When a type or test run hits a bug outside your code, fixing it is out of scope and can hide the failure. Mark the test xfail strict so an accidental pass fails loudly, add one line to BUGS.md with where and why, and move on.

## Stage files by path
`git add -A` sweeps in half-finished work from a crashed session and the next item's files into this item's commit. Stage exactly the files of the item plus PROGRESS-<name>.md with `git add <path>`, one path at a time.

## No dashes in specs
Agents copy punctuation from their instructions straight into code, docs and commit messages; em and en dashes end up everywhere and break diffs and greps. Specs are dash free by rule, and the textlint step refuses rendered files that contain them.

## Stop after three empty sessions
A supervisor that keeps relaunching an agent which produces nothing burns tokens forever on a dead end. After three consecutive sessions with no new item done, stop and report; a human should look at what is stuck.

## Keep the why when you rewrite text
When an agent rewrites a docstring or comment it tends to drop the design reason that lived in the old wording. The rule: move the reason word for word into a comment instead of deleting it. The code may change; the why stays.

## Notes files are append-only
BUGS.md, IDEAS.md and BLOCKED.md only grow. A session that rewrites or shortens one silently loses history; in a real run a bug report disappeared when an agent tidied the file. Add lines, never rewrite.

## The machine can fail, not only the agent
In Sparton run 13 the machine itself failed late in the night: Windows refused to start new processes for a while, and every process creation came back with exit code 3221225794 (0xC0000142, STATUS_DLL_INIT_FAILED). The agent launch failed and so did the status command, so the supervisor saw three sessions that never started, counted them as empty, and gave up with a false "agent stuck" although nothing was wrong with the agent. Two rules came from it: an unreadable status is unknown, not stopped (the supervisor keeps polling instead of ending the session), and a failed launch is retried after a backoff that doubles each time; it never counts as an empty session, and only six consecutive failed starts end the run.

## One supervisor per repo
On 2026-11-16 a stray supervisor from an earlier attempt was still alive on this repo when a new run started, and when the new run's agent session ended, the old supervisor relaunched its own run's prompt on the new run's branch, so the agent redid old items there. The lock therefore lives in the repo's git directory instead of the run folder: it guards the repo, not the run, so every run dir that points at the same repo shares one lock and only one supervisor can work on a given repo at a time.

## Watching a run needs no shell loop
A hand-written `while true; do ...; sleep 20` loop to watch an overnight run is exactly what `nightshift watch` replaces, so never write one by hand.