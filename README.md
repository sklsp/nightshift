# nightshift

nightshift runs a local LLM coding agent unattended: you write one item per spec file, and the agent works through them in order while nobody watches. It renders the run files from a small config plus an items list, gives the agent a test gate that fits inside a 30 second command limit, and supervises the agent by relaunching it when a session ends. In the morning you get one commit per item and a progress file with the last pytest line next to each tick.

What a morning looks like, from real runs on another project (`nightshift watch`, then `nightshift report`):

```
$ nightshift watch myrun
commit 4f336e3 Q1: dashboard: the slowest tools in the Speed panel
commit 949d410 Q2: docs: the Speed panel shows tools
watch: run finished

$ nightshift report myrun
# qwen64: 2/2 items
- Q1 dashboard: the slowest tools in the Speed panel: 4f336e3
- Q2 docs: the Speed panel shows tools: 949d410 (3 min)
run time: 3 min (first to last item commit)

## Review
- no warnings

## Supervisor
- 1 session
```

docs/ARCHITECTURE.md explains how nightshift works inside: the life of a run, every module and the supervisor's rules.
The suite includes an end to end test that drives a fake agent through a real run: supervise launches it as a subprocess and watches it commit every item until the progress file is fully ticked.


## Install

From the repository:

```
pip install git+https://github.com/sklsp/nightshift
```

For development, from a clone of the repo:

```
pip install -e .[dev]
```

Python 3.11 or newer; no other dependencies. This puts `nightshift` on your PATH.

## Quick start

1. Create a run directory:

   ```
   nightshift init myrun
   nightshift init myrun --from old-run
   ```

   This writes `myrun/run.toml` (every key shown, commented) and an example `myrun/items.md`; with `--from`, it copies the old run's two files instead, rewriting only name and branch when you pass --name or --branch, and starts items.md as an empty skeleton.
2. Edit the two files: point `repo` at your repo, set the test command in `[check]`, then write one item per section in items.md (`## title` plus a body).
3. Check that config, items, repo and test program are ready before starting:

   ```
   nightshift doctor myrun
   ```

4. Render the run files:

   ```
   nightshift build myrun
   ```

5. Point your agent at `myrun/00-start.md` and let it work, or drive it yourself with the supervisor; in another terminal, watch progress as it happens:

   ```
   nightshift supervise myrun
   nightshift watch myrun
   ```

6. When every item is ticked, print a markdown summary for a pull request body:

   ```
   nightshift report myrun
   ```

To run several runs back to back, use `nightshift queue <run dir> [<run dir> ...]`: it first checks that every run dir exists with a run.toml, then supervises them in order and stops at the first non-zero exit code.

The supervisor survives a machine that refuses to start processes: an unreadable status is treated as unknown and a failed launch is retried with backoff; see `agent.launch_backoff_seconds` and `agent.max_launch_failures` in the config reference.

The supervisor takes a lock file in the repo's git directory before it starts, so only one supervisor can work on a repo at a time; while another process holds the lock, `supervise` refuses with exit code 2.

The supervisor appends one JSON object per poll event to `myrun/.gate/supervise.jsonl`, so a finished run leaves an audit trail of what it saw. When that log has entries, `status` adds a Supervisor: section with the last five events, one per line. And `report` shows how long each item took from its commit times plus a run time line from first to last item commit. It also adds a Review section between run time and Supervisor with every warning review would print, prefixed by the item id; when there are none it says `- no warnings`, so a reader knows the check ran. When that log has entries, `report` ends with a Supervisor section holding the same last five events. And `status` says how long the agent has been on its current item: it counts up from the newest commit of a done item or, when there is none, from the first supervisor event; `--json` carries whole minutes under current_minutes.

## The gate

Agent terminals kill any command that runs longer than about 30 seconds. The gate starts a check in a detached process instead; each call returns within the limit and picks the result up from files:

```
nightshift gate start myrun          # run lint, then the test command, detached
nightshift gate wait myrun 1         # poll for the result (use a new number every time)
nightshift gate wait myrun 2
nightshift gate file myrun tests/test_gate.py nightshift.gate   # one test file with coverage of one module
nightshift gate clean myrun          # remove .gate state when nothing is running
```

`wait` prints the pytest summary line and says GATE PASS or GATE FAIL. The log lives in `myrun/.gate/log`; a new start refuses to double start while the previous run is still going. A run whose log is older than two minutes without a result is treated as stale: start relaunches instead of refusing, and wait reports GATE STALE instead of polling forever.

## Watching a run

`nightshift watch myrun` prints new commits and supervisor events as they appear, so you can leave it open in a terminal while the agent works:

```
commit 3f9c2ab O1: add the gate wait command
04:12:30 session 2 ended: 1/3 done, 0 blocked
commit 8d1e77c O2: tick and commit item one
watch: run finished
```

It prints nothing for commits and events that already exist when it starts, polls every `--every` seconds (default 20, at least 1), ends with the line `watch: run finished` when the progress file is fully ticked, and returns 0 on Ctrl+C. `nightshift watch myrun --once` does one poll that prints what exists now, all events plus the last five commits, and exits.

## Run folder layout

```
myrun/                  the run directory, outside the repo
  run.toml              config for this run
  items.md              one section per item; you edit it
  00-start.md           rendered: what the agent reads first
  item-01.md ...        rendered: one spec file per item
  checklist.txt         rendered: unticked lines, seeds PROGRESS-myrun.md
  .gate/                gate state while a check runs (log, done, mode)

<repo>/PROGRESS-myrun.md   in the repo root; ticked with each commit
```

## Your own templates

`nightshift build` renders `00-start.md` and the item files from two built-in templates in the package. To change their wording, put your own copies named `start.md` and `item.md` into a directory (for example `<run dir>/tpl`) and set `templates = "tpl"` under `[run]`; any file you do not provide falls back to the built-in one. The placeholders each template receives:

- start.md
  - `$name`: the run name from `run.name`.
  - `$branch`: the branch the agent must work on; the repo's current branch when unset, then "main".
  - `$prefix`: item id prefix (T1, T2, ...).
  - `$total`: how many items the run has.
  - `$first_item`: path of the first item file in the run dir.
  - `$items_list`: one line per item file path.
  - `$checklist_file`: path of checklist.txt in the run dir.
  - `$progress_file`: path of PROGRESS-<name>.md in the repo root.
  - `$check_command`: the check command (lint, pre and test) as one line.
  - `$command`: the nightshift command prefix from `run.command`.
  - `$run_dir`: absolute path of the run directory.
  - `$timeout_hint`: per-command time limit in seconds from `check.timeout_hint_seconds`.
- item.md
  - `$name`: the run name from `run.name`.
  - `$number`: this item's number.
  - `$total`: how many items the run has.
  - `$title`: the item title from its `##` heading in items.md.
  - `$body`: the item body text from items.md.
  - `$id`: the item id (prefix plus number, for example T1).
  - `$start_file`: path of 00-start.md in the run dir.
  - `$progress_file`: path of PROGRESS-<name>.md in the repo root.
  - `$command`: the nightshift command prefix from `run.command`.
  - `$ending`: "send the final message" on the last item, otherwise "open the next item file".
  - `$run_dir`: absolute path of the run directory.

## Config reference (run.toml)

| key | default | meaning |
| --- | --- | --- |
| `run.name` | required | short id for PROGRESS-<name>.md and item ids (letters, digits, dashes) |
| `run.repo` | required | path to the repo root, relative to the run dir or absolute |
| `run.branch` | `""` | when set, refuse to start unless the repo is on this branch (checked by supervise and doctor) |
| `run.base` | `""` | range base for the commit check and report, as a branch or commit; when unset, merge-base of run.branch and HEAD if it differs from the current branch, else main/master (or their origin copies) when present, else the first commit |
| `run.prefix` | `"T"` | item id prefix: T1, T2, ... |
| `run.command` | `["nightshift"]` | program the rendered spec tells the agent to run; for example `["/path/to/venv/bin/python", "-m", "nightshift"]` when it is not on PATH |
| `run.templates` | `""` | directory of your own start.md and item.md templates, relative to this run dir or absolute; see "Your own templates" |
| `run.protect` | `[]` | repo-relative posix path prefixes the agent must never touch (for example `.github/`); the commit check flags any commit that changes a matching file |
| `check.test` | required | test command, run from the repo root |
| `check.lint` | `[]` | optional lint command that must pass before tests start |
| `check.pre` | `[]` | extra commands run after lint and before the tests, each a list of strings; first failure stops the start |
| `check.min_passed` | `1` | minimum passed tests for a green gate on `gate start` |
| `check.wait_seconds` | `25` | how long one `gate wait` call may block |
| `check.timeout_hint_seconds` | `30` | the command time limit the agent works under (informational) |
| `agent.launch` | `[]` | command that starts one agent session; `{message}` is replaced by the resume message |
| `agent.launch_ok_codes` | `[0]` | launch exit codes that do not get a log line; any other code is logged |
| `agent.status` | `[]` | command whose output the supervisor polls for a live agent |
| `agent.running_pattern` | `"running"` | substring in status output meaning the agent is alive |
| `agent.poll_seconds` | `30` | how often the supervisor checks the agent and progress |
| `agent.max_sessions` | `20` | sessions allowed before the supervisor gives up |
| `agent.launch_grace_polls` | `4` | status polls to wait for a launched agent to report running; if it never does, the supervisor backs off and relaunches without counting the session as empty |
| `agent.idle_minutes` | `60` | minutes without a repo change (HEAD plus git status) before the supervisor reports the agent stuck |
| `agent.stop` | `[]` | command run once to end a session when it looks stuck; empty means do nothing |
| `agent.status_retry_polls` | `10` | status polls in a row that may fail (the status command itself errors) before the session counts as ended; an unreadable status is unknown, never "stopped" |
| `agent.launch_backoff_seconds` | `60` | seconds to wait before relaunching a failed agent start; doubles each failure up to 15 minutes; each failed start uses one of `max_sessions` |
| `agent.max_launch_failures` | `6` | consecutive failed starts before the supervisor gives up with exit code 2 |

Note for Cline users: the launch command may print "zen dispatch failed" while the session still starts; nightshift does not treat launch output as failure.

## Lessons

docs/LESSONS.md collects the rules learned from real overnight runs. Read it before your first run.

## How this was built

nightshift was built the way it runs. A local model (Qwen 3.8 27B in LM Studio, driven through the Cline CLI) wrote
most of the code, item by item, in unattended runs; Claude (Anthropic) wrote the specs, reviewed every commit,
fixed what the review found and decided what came next; I set the direction and own the design. The history shows
it: each run's commits are numbered by item (`J1:`, `K2:` and so on), and the review fixes follow as separate
commits. From version 0.12 on, nightshift supervised its own runs on other projects, and most features after that
came from what those runs got wrong (see docs/LESSONS.md).

## License

MIT, see LICENSE.
