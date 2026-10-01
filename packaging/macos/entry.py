"""Standalone entry point. Keep freeze_support ahead of heavyweight imports."""
import multiprocessing
import os
from pathlib import Path
import sys

# Each tile already runs in its own process: BLAS must not create another pool per tile.
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ.setdefault(variable, '1')
cache = Path.home()/'Library/Caches/Cartografiler/numba'
os.environ.setdefault('NUMBA_CACHE_DIR', str(cache))
cache.mkdir(parents=True, exist_ok=True)

if __name__ == '__main__':
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == '--package-check':
        from branchfm.package_checks import check_package
        check_package(Path(sys.argv[2]))
        raise SystemExit(0)
    if len(sys.argv) == 1:
        sys.argv.append(str(Path.home()))
    log_dir = Path.home()/'Library/Logs/Cartografiler'
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir/'application.log').open('a', buffering=1)
    sys.stdout = sys.stderr = log
    from branchfm.service import launch
    raise SystemExit(launch())
