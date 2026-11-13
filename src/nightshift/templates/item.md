nightshift run $name, item $number of 1 to $total. Rules: $start_file.

$number. **$title**
$body

WHEN DONE: run the check from $start_file until it passes, tick $id in $progress_file with the last pytest line,
commit (stage only the item's files and $progress_file), then $ending
