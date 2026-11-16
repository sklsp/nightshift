# Notes for nightshift8

Run nightshift8 released version 0.8.0 with three changes, one commit each:

- K1 (a36a8ed): supervise appends one JSON object per poll event to .gate/supervise.jsonl, so a finished run leaves an audit trail of what the supervisor saw.
- K2 (6fb522e): status shows a Supervisor: section with the last five events from that log when it has entries; --json carries them under an "events" key.
- K3 (5c4632e): report shows how long each item took from its commit times and a run time line from first to last item commit.
- K4 (983a9cd): README documents the event log, the new status lines and the durations; version 0.8.0 in pyproject.toml, __init__.py and the version test; CHANGELOG entry naming the three changes.

Final check: ruff clean, 181 passed (the suite started this run at 172; K1 added 2 tests, K2 four and K3 three).