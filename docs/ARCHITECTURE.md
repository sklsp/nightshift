# Architecture

How nightshift works inside, from the code in `src/nightshift/`. Each claim below names the file and function that proves it; when a rule exists because of a real failure, docs/LESSONS.md has the story.

## The life of a run

A run is a folder outside the repo with two files you write: `run.toml` (the config) and `items.md` (one section per item). `nightshift init <dir>` creates both as examples (`cli.py`, `_init`). `load_config` in `src/nightshift/config.py` reads run.toml into a frozen `RunConfig`; unknown keys, wrong types or missing required values raise `ConfigError`. `load_items` in `src/nightshift/items.py` splits items.md at its `## ` headings into numbered `Item`s and refuses empty titles, duplicate titles and empty bodies.

`nightshift build <dir>` renders the spec files (`cli.py`, `_build`) by calling `render_run` in `src/nightshift/render.py`: it writes `00-start.md`, one `item-NN.md` per item and an unticked `checklist.txt`. The text comes from two templates, `start.md` and `item.md`; when `run.templates` names a directory, files there win over the built-in copies in the package (`render.py`, `_template`). Every rendered file passes through `lint_files` in `src/nightshift/textlint.py`, which refuses em dashes, en dashes and trailing whitespace. When `run.protect` is set, the start spec gains a rule line naming those paths (`render.py`, `_protected_line`).

The agent then works item by item. `00-start.md` tells it to open only the file of the item it is working on; each item spec ends with "open <next> and do that item. Do not look at it before this one is committed." (`render.py`, `_item_text`). After finishing an item the agent commits it and ticks its line in `PROGRESS-<name>.md` in the repo root, which `read_progress` in `src/nightshift/progress.py` parses into done, blocked and next.

The check runs behind the gate because agent terminals kill commands after about 30 seconds while a real suite takes minutes. `gate start` (`gate.py`, `start`) first runs lint and every pre check in order; when they pass it launches the test command detached through a second nightshift process (`_launch`, `_detach_kwargs`). Output goes to `.gate/log`, the exit code to `.gate/done`. `gate wait <n>` blocks at most `check.wait_seconds` (default 25), then either prints the next command with number n+1 or parses the pytest summary line from the log and says GATE PASS or FAIL (`wait`, `parse_summary`).

While the agent works, `nightshift supervise <dir>` keeps it alive: `supervise()` in `src/nightshift/supervise.py` polls the status command every `agent.poll_seconds`, relaunches with a resume message when a session ends, and stops when every item is ticked or blocked. The read-only commands look at what happened afterwards: `watch` prints new commits and supervisor events until the final tick is committed (`src/nightshift/watch.py`), `status` prints a one-screen summary including minutes on the current item (`cli.py`, `_status`; `current_item_seconds` in `src/nightshift/status.py`), and `report` builds a markdown summary for a pull request body (`build_report` in `src/nightshift/report.py`), `review` prints what each item commit changed with warning lines under each block (`build_review` in `src/nightshift/review.py`), and `history` prints one row per run dir plus a total line across several runs (`history_lines` in `src/nightshift/history.py`).

```mermaid
sequenceDiagram
    participant Y as you
    participant N as nightshift
    participant A as agent
    participant G as gate
    participant R as repo
    Y->>N: write run.toml and items.md, then build
    N-->>Y: 00-start.md, item files, checklist.txt
    Y->>N: supervise
    loop one session
        N->>A: launch with the resume message
        A->>R: work on the current item
        A->>G: gate start, then gate wait n
        G-->>A: GATE PASS or FAIL from .gate files
        A->>R: commit and tick the PROGRESS file
        N->>A: poll status every poll_seconds
    end
    Note over N,A: session ends; the supervisor relaunches until finished
    Y->>N: watch, status or report read the result
```

## The modules

| file | what it does |
| --- | --- |
| `src/nightshift/cli.py` | Command line interface: builds the argparse tree for init, build, supervise, queue, status, watch, doctor, report, history, commits, review, finish and gate (`build_parser`, `main`) and dispatches to the other modules. |
| `src/nightshift/config.py` | Loads and validates run.toml into a frozen `RunConfig`; unknown keys, wrong types or missing required values raise `ConfigError`. |
| `src/nightshift/items.py` | Splits items.md at its `## ` headings into numbered `Item`s; empty titles, duplicate titles or empty bodies raise `ItemsError`. |
| `src/nightshift/render.py` | Renders 00-start.md, one item file per item and checklist.txt from templates (`render_run`); raises `RenderError` when a rendered file fails the text check. |
| `src/nightshift/textlint.py` | Flags em dashes, en dashes and trailing whitespace in rendered spec files so agents never copy them into code or commits. |
| `src/nightshift/gate.py` | Runs a check behind the gate: lint plus pre checks first (`start`), then the test command detached; `wait` reads .gate/log and .gate/done and says GATE PASS or FAIL. |
| `src/nightshift/agent.py` | Launches an external agent with `{message}` substituted into its command (`launch`) and polls status output for a running pattern (`is_running`). |
| `src/nightshift/supervise.py` | Relaunches the agent until every item is done or a stop condition hits; owns the repo lock, idle detection and the .gate/supervise.jsonl event log. |
| `src/nightshift/watch.py` | Prints new commits and supervisor events as they appear until the run is finished and its final tick is committed (`watch`). |
| `src/nightshift/status.py` | Computes how long the agent has been on its current item from commit times or the first supervisor event (`current_item_seconds`). |
| `src/nightshift/report.py` | Builds a markdown summary of a run for a pull request body: per-item commits, run time, gate line and supervisor counts (`build_report`). |
| `src/nightshift/history.py` | Summarises several runs as one row per run dir plus a total line, reusing report's numbers for items, run time, sessions and warnings; missing dirs are noted and skipped (`history_lines`). |
| `src/nightshift/review.py` | Prints what each item commit changed: files with added and removed lines, test counts before and after the commit, and warning lines under each block (`build_review`). |
| `src/nightshift/commits.py` | Checks that the history has exactly one well-formed commit per item since base; reports bad subjects, empty bodies and missing or doubled items (`commit_report`). |
| `src/nightshift/git.py` | Small helpers around git: `run_git`, `current_branch`, `git_dir`, `fingerprint` (HEAD plus status text) and `commit_log`. |
| `src/nightshift/progress.py` | Reads PROGRESS-<name>.md into a `Progress` with done, blocked and next item; an unticked line ending in BLOCKED counts as resolved. |
| `src/nightshift/finish.py` | Drops PROGRESS-<name>.md once every item is done or blocked: refuses while a live supervisor holds the repo lock, when items are still open, on uncommitted tracked changes or on the wrong branch, then commits the removal (`run_finish`). |

## The supervisor's rules and why each exists

Every rule below lives in `supervise()` in `src/nightshift/supervise.py` unless noted; docs/LESSONS.md has the story behind each one.

- **Unknown status.** `agent.is_running` in `src/nightshift/agent.py` returns True when the status output matches `running_pattern`, False when it does not, and None when the command fails or times out (10 seconds). The supervisor counts consecutive unknown polls and only treats the session as ended after `status_retry_polls` of them (default 10); an unreadable status is never "stopped". Why: a machine that refuses to start processes makes launch and status fail together, so counting those as dead sessions gave up on nothing wrong with the agent.
- **Launch backoff.** After a launch, the supervisor waits `launch_grace_polls` polls (default 4) for the agent to report running; when it never does, the start counts as failed. It then sleeps `launch_backoff_seconds * 2^(failures - 1)` capped at 900 seconds before trying again, and a failed start never counts as an empty session. Six consecutive failed starts (`max_launch_failures`) end the run with exit code 2.
- **Idle detection.** On every poll the supervisor fingerprints the repo: HEAD hash plus git status porcelain text (`fingerprint` in `src/nightshift/git.py`). When the fingerprint does not change for `idle_minutes` (default 60), it logs an "agent looks stuck" event and runs the optional `agent.stop` command once, then waits for the session to end. Why: a compacted context can leave an agent re-reading files for hours without writing anything.
- **Empty sessions.** After each ended session the supervisor compares how many items are done; three consecutive sessions with no new item done stop it with exit code 1 and a line saying a human should look. `max_sessions` (default 20) caps the total number of sessions either way.
- **The repo lock.** Before its first poll, supervise writes `<git dir>/nightshift-supervise.lock` holding its pid and run dir (`_take_lock`). A live pid in an existing lock means another supervisor is running: this one refuses with exit code 2. A dead or unreadable lock is removed as stale and taken over. The lock lives in the repo's git directory, not the run folder, so every run dir that points at the same repo shares one lock: it guards the repo, not the run.
- **The branch check.** When `run.branch` is set and differs from what `current_branch` reports, supervise refuses to start with exit code 2 before taking the lock; `doctor` reports the same mismatch (`cli.py`, `_branch_check`).

## The files a run leaves behind

- `PROGRESS-<name>.md` in the repo root: one line per item, ticked and committed with each item. It is the single source of truth for done, blocked and next (`read_progress` in `src/nightshift/progress.py`).
- `.gate/` in the run dir, created while a check runs or a supervisor works:
  - gate state: `log` (test output), `done` (exit code), `mode` (start, or file plus module) and `command` (the saved command prefix); `state()` in `src/nightshift/gate.py` reads them into pass, fail, running, stale or none.
  - `progress.md`: a copy of the repo progress file refreshed on every supervisor poll (`_refresh_progress_copy`), so a finished run stays finished if cleanup removes the repo file; `read_run_progress` falls back to it.
  - `supervise.jsonl`: one JSON object per supervisor event with timestamp and message, appended by `_log_with_events`; `read_supervisor_events` reads it back for status, watch and report.

`gate clean` deletes `.gate/` when it exists (`clean` in `src/nightshift/gate.py`).


