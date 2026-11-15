nightshift run $name, item $number of 1 to $total. Rules: $start_file.

$number. **$title**
$body

If the check takes longer than one command allows, start it with `$command gate start $run_dir` and poll with
`$command gate wait $run_dir 1`.

WHEN DONE: run the check from $start_file until it passes, tick $id in $progress_file with the last pytest line,
commit (stage only the item's files and $progress_file), then $ending
