#!/bin/sh
set -eu
task06_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$task06_root"
export PYTHONPATH="$task06_root/src:$task06_root"
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
if [ -x "$task06_root/.venv-task04/bin/python" ]; then
    export DYLD_LIBRARY_PATH="$task06_root/.venv-task04/lib/python3.12/site-packages/sklearn/.dylibs"
    exec "$task06_root/.venv-task04/bin/python" "$@"
fi
exec python3 "$@"
