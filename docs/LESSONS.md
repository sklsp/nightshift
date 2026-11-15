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