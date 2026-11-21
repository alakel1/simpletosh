#!/usr/bin/env bash
# Developer aid: open the installer on each page and save a PNG screenshot.
# Usage: tools/screenshot-pages.sh OUTDIR [report.json] [pages...]
set -u
OUT=${1:?outdir}; shift
REPORT=${1:-}; [ $# -gt 0 ] && shift
PAGES=${*:-welcome hardware versions config target confirm done}
mkdir -p "$OUT"
cd "$(dirname "$0")/.."
for p in $PAGES; do
  OCL_STEP=$p OCL_REPORT=$REPORT python3 -m oclauncher gui 2>"$OUT/$p.log" &
  pid=$!
  wid=""
  for _ in $(seq 1 40); do
    wid=$(xdotool search --sync --onlyvisible --pid $pid 2>/dev/null | head -1) && [ -n "$wid" ] && break
    sleep 0.25
  done
  sleep 2.5
  [ -n "$wid" ] && import -window "$wid" "$OUT/$p.png"
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
  echo "$p -> $OUT/$p.png"
done
