#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  printf 'Usage: bash %s /path/to/molt /path/to/molt-kernel\n' "$0" >&2
  exit 2
fi

cli=$(realpath -e -- "$1")
kernel=$(realpath -e -- "$2")
test -x "$cli"
test -x "$kernel"
command -v jq >/dev/null

base=$(mktemp -d "${TMPDIR:-/tmp}/molt-demo.XXXXXX")
base=$(realpath -e -- "$base")
printf 'Demo directory: %s\n' "$base"

v() { "$cli" version --workspace "$base/w" "$@"; }
p() { "$cli" project --config "$base/project.json" "$@"; }

v init --kernel "$kernel" --synthetic
"$cli" version --workspace "$base/w" serve --kernel "$kernel" >"$base/kernel.log" 2>&1 &
kernel_pid=$!
cleanup() {
  kill "$kernel_pid" 2>/dev/null || true
  wait "$kernel_pid" 2>/dev/null || true
  if [[ -S "$base/w/state/kernel.sock" ]]; then
    unlink "$base/w/state/kernel.sock"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ready=0
for attempt in {1..100}; do
  if v status >"$base/status.json" 2>"$base/status.err"; then
    ready=1
    break
  fi
  kill -0 "$kernel_pid" 2>/dev/null || break
  sleep .05
done
if (( ! ready )); then
  printf 'Workspace did not start. See %s/kernel.log and %s/status.err\n' "$base" "$base" >&2
  exit 1
fi

mkdir -m 700 "$base/edit" "$base/exports"
cat >"$base/project.json" <<'JSON'
{"workspace":"w","edits":"edit","exports":"exports","files":["settings.json","prompt.txt"]}
JSON

edit=$(p begin | jq -er '.edit')
printf '%s\n' '{"mode":"short"}' >"$edit/settings.json"
printf '%s\n' 'Write a summary' >"$edit/prompt.txt"
p save --reason 'Save a short baseline' >"$base/a.json"

printf '%s\n' '{"mode":"full"}' >"$edit/settings.json"
printf '%s\n' 'Write a full report' >"$edit/prompt.txt"
p preview >"$base/preview.json"
p save --reason 'Use the full report format' >"$base/b.json"
p read-tree >"$base/pinned-b.json"

p rollback "$(jq -er '.snapshot' "$base/a.json")" >"$base/rollback.json"
p read-tree >"$base/pinned-a.json"

# The reader receives one export and only needs to understand its own files.
read_job() {
  jq -er '.mode' "$1/settings.json"
  cat "$1/prompt.txt"
}

snapshot_a=$(jq -er '.snapshot' "$base/a.json")
snapshot_b=$(jq -er '.snapshot' "$base/b.json")
test "$snapshot_a" != "$snapshot_b"
test "$(jq -er '.snapshot' "$base/pinned-a.json")" = "$snapshot_a"
test "$(jq -er '.snapshot' "$base/pinned-b.json")" = "$snapshot_b"
read_job "$(jq -er '.tree' "$base/pinned-b.json")" >"$base/old-job.txt"
read_job "$(jq -er '.tree' "$base/pinned-a.json")" >"$base/new-job.txt"
diff -u <(printf '%s\n' full 'Write a full report') "$base/old-job.txt"
diff -u <(printf '%s\n' short 'Write a summary') "$base/new-job.txt"

v audit "$(jq -er '.event' "$base/b.json")" >"$base/audit-b.json"
reason=$(jq -er '.evidence.reason.text' "$base/audit-b.json")
test "$reason" = 'Use the full report format'
printf '\nPreviously pinned job (%s)\n' "$snapshot_b"
cat "$base/old-job.txt"
printf '\nNew job after rollback (%s)\n' "$snapshot_a"
cat "$base/new-job.txt"
printf '\nAudit reason: %s\n' "$reason"
