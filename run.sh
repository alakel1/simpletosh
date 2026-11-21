#!/usr/bin/env bash
# Start the graphical installer from the source tree.
#   ./run.sh                 -> GUI (non-root: folder mode only)
#   sudo ./run.sh            -> GUI with USB / disk writing enabled
#   ./run.sh analyze         -> text compatibility report
#   ./run.sh build --out DIR -> build an EFI from the command line
cd "$(dirname "$0")" && exec python3 -m oclauncher "$@"
