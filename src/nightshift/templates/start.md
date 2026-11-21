nightshift run $name: items 1 to $total, ONE item at a time, in the repo from run.toml, branch `$branch`.
Stay on `$branch`. Never push, never create or switch branches. Work unattended for several hours, never ask questions,
never stop early. Your FIRST action is a tool call. Do NOT finish between items. Finish only when item $total is done.

WHY
A nightshift run packages work for an unattended coding agent: the run directory holds run.toml and items.md; this
tool renders one spec file per item plus a checklist, gives the agent a test gate that works inside a 30-second
command limit, supervises the agent (restarts it when a session ends, stops when every item is ticked), and reports
progress. You are building or changing code in the repo while nobody watches: keep it small, clear, typed and tested.

HOW THIS RUN WORKS
- Each item is in its own file, one per line below. Open ONLY the file of the item you are working on.
  Never open the next item file before the current item is committed and ticked.
$items_list
- Progress lives in $progress_file (created by this render; it is committed with each item).
  Start: if it does not exist, create it with the lines of $checklist_file (unticked), then open $first_item.
  Resume: find the first item that is not ticked [x] and not BLOCKED, open its file. If the working tree already
  holds changes for that item, a session before you made them: review `git diff --stat` and `git status` once,
  finish what is missing, run the check.
- After each item: tick it as `- [x] ${prefix}<n>. <title> (<last pytest line>)` in the same commit, then open the next
  item file straight away. No summaries between items.

THE CHECK (cwd = repo; before EVERY commit, it must pass)
- `$check_command`
- The lint part prints its all-pass line and the test part ends with `N passed` and 0 failed. When the full check takes
  longer than $timeout_hint seconds, run it through the gate, never directly; one test file directly is fine. Do not add
  flags that hide the summary line of the test part.

THE GATE
Your commands time out after 30 seconds. If a check takes longer than one command allows, run it through the gate:
start it once with `$command gate start $run_dir`, then poll with `$command gate wait $run_dir 1` and again
with `$command gate wait $run_dir 2`. Each call returns within the command limit; when the check is done, the
gate says pass or fail. Extra arguments for the test command go after a literal `--` and apply to this one gate
only: `$command gate start $run_dir -- <extra args>`. One test file with coverage of one module:
`$command gate file $run_dir <test file> <module>`.
Check progress any time with `$command status $run_dir`. If a direct run takes longer than the limit, your tests are too slow: fix them.

RULES
- **Only these items.** Ideas go as one line in IDEAS.md in the run directory. Never re-scope an item.
$protected
- **Tests:** every behaviour an item names gets a test that fails if the behaviour breaks. Literal expected values.
  Never delete or weaken a test from an earlier item; if a later item changes behaviour on purpose, update the test
  and say why in the commit body.
- **Keep the why:** when you rewrite a docstring or comment, keep any design reason in the old text: move it word for
  word into a comment. Never delete it.
- **Notes are append-only:** BUGS.md, IDEAS.md and BLOCKED.md only grow. Add lines; never rewrite or shorten them. A real
  run lost a bug report this way.
- **Commits:** one commit per item, message `${prefix}<n>: <title>` (for example `${prefix}1: fix the parser`), body = the last
  pytest line. The tick goes in the same commit. Stage only the files of the item plus $progress_file, with
  `git add <path>`. Never `git add -A` or `git add .`. Never `--amend`.
- **Terminal:** code must work on Windows and Linux (pathlib, no shell=True unless an item says so, subprocess
  with lists). Chain commands the way your shell requires.
- **Never kill processes by name** (no `Stop-Process -Name python`, `taskkill /IM python.exe`, `pkill python`): that also
  kills the supervisor that keeps this run alive and the user's own programs. Stop only a process you started yourself,
  by the pid you saved when you started it.
- **Never push** (no `git push`, no new remote branches): the operator reviews, rebases and pushes after the run.
- **Text:** no em dashes or en dashes anywhere, in code, docs or commit messages. Plain, short English.
- **Never** run git checkout, switch, branch, merge, rebase, stash, reset, restore, clean or push.
- **Failures:** if an item fails 3 times, write what you tried to BLOCKED.md in the run directory, undo your
  uncommitted edits of that item by hand, tick it as `- [ ] ${prefix}<n>. ... BLOCKED`, commit that, go on.

**Final message:** items 1 to $total with a commit hash each (or BLOCKED), the last pytest line, and a section
"Not done exactly as written:" (write "none" if there are none).
