# nightshift

nightshift runs a local LLM coding agent unattended: you write one item per spec file, and the agent works through them in order while nobody watches. It renders the run files from a small config plus an items list, gives the agent a test gate that fits inside a 30 second command limit, and supervises the agent by relaunching it when a session ends. In the morning you get one commit per item and a progress file with the last pytest line next to each tick.

## Install

```
pip install .
```

Python 3.11 or newer; no other dependencies. This puts `nightshift` on your PATH.

## Quickstart

1. Create a run directory:

   ```
   nightshift init myrun
   ```

   This writes `myrun/run.toml` (every key shown, commented) and an example `myrun/items.md`.
2. Edit the two files: point `repo` at your repo, set the test command in `[check]`, then write one item per section in items.md (`## title` plus a body).
3. Render the run files:

   ```
   nightshift build myrun
   ```

4. Point your agent at `myrun/00-start.md` and let it work, or drive it yourself with the supervisor:

   ```
   nightshift supervise myrun
   nightshift status myrun
   ```

## The gate

Agent terminals kill any command that runs longer than about 30 seconds. The gate starts a check in a detached process instead; each call returns within the limit and picks the result up from files:

```
nightshift gate start myrun          # run lint, then the test command, detached
nightshift gate wait myrun 1         # poll for the result (use a new number every time)
nightshift gate wait myrun 2
nightshift gate file myrun tests/test_gate.py nightshift.gate   # one test file with coverage of one module
```

`wait` prints the pytest summary line and says GATE PASS or GATE FAIL. The log lives in `myrun/.gate/log`; a new start refuses to double start while the previous run is still going.

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

## Config reference (run.toml)

| key | default | meaning |
| --- | --- | --- |
| `run.name` | required | short id for PROGRESS-<name>.md and item ids (letters, digits, dashes) |
| `run.repo` | required | path to the repo root, relative to the run dir or absolute |
| `run.branch` | `"main"` | branch to work on; never pushed |
| `run.prefix` | `"T"` | item id prefix: T1, T2, ... |
| `check.test` | required | test command, run from the repo root |
| `check.lint` | `[]` | optional lint command that must pass before tests start |
| `check.min_passed` | `1` | minimum passed tests for a green gate on `gate start` |
| `check.wait_seconds` | `25` | how long one `gate wait` call may block |
| `check.timeout_hint_seconds` | `30` | the command time limit the agent works under (informational) |
| `agent.launch` | `[]` | command that starts one agent session; `{message}` is replaced by the resume message |
| `agent.status` | `[]` | command whose output the supervisor polls for a live agent |
| `agent.running_pattern` | `"running"` | substring in status output meaning the agent is alive |
| `agent.poll_seconds` | `30` | how often the supervisor checks the agent and progress |
| `agent.max_sessions` | `20` | sessions allowed before the supervisor gives up |

## Lessons

docs/LESSONS.md collects the rules learned from real overnight runs. Read it before your first run.
