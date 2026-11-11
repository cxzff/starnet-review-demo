#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
exec .venv/bin/python scripts/start_services.py
