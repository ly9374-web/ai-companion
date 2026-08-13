#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_dir="$script_dir/.venv"

if [ ! -x "$venv_dir/bin/python" ]; then
  python3 -m venv "$venv_dir"
  "$venv_dir/bin/python" -m pip install -r "$script_dir/requirements.txt"
fi

exec "$venv_dir/bin/python" "$script_dir/app.py"
