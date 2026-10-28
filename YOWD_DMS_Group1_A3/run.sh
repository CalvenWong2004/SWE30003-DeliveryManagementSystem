#!/bin/sh
cd "$(dirname "$0")"
PYTHONPATH=src python3 -m yowd_dms "$@"
