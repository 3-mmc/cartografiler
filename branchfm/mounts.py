"""Data disks promoted to continents on Linux/WSL and macOS."""
from functools import lru_cache
from . import platform as host
import sys
import time


def parse_disk_mounts(text: str) -> tuple[str, ...]:
    return _linux_disks(host.parse_mount_table(text))


def _linux_disks(table: list[tuple[str, str]]) -> tuple[str, ...]:
    roots = set()
    for path, kind in table:
        if not path.startswith(('/mnt/', '/media/', '/run/media/')):
            continue
        if path == '/mnt/wsl' or path.startswith('/mnt/wsl/') or path == '/mnt/wslg' or path.startswith('/mnt/wslg/'):
            continue
        if kind in {'tmpfs', 'ramfs', 'autofs', 'overlay', 'proc', 'sysfs'}:
            continue
        roots.add(path.rstrip('/'))
    # Nested mounts belong to their outer disk, rather than overlapping continents.
    return tuple(p for p in sorted(roots) if not any(p.startswith(q+'/') for q in roots if q != p))


@lru_cache(maxsize=1)
def _disk_mounts(bucket: int) -> tuple[str, ...]:
    if sys.platform == 'darwin':
        roots = {path.rstrip('/') for path, kind in host.mount_table()
                 if path.startswith('/Volumes/') and kind not in {'autofs', 'devfs'}}
        return tuple(p for p in sorted(roots) if not any(p.startswith(q+'/') for q in roots if q != p))
    if sys.platform.startswith('linux'):
        return _linux_disks(host.mount_table())
    return ()


def disk_mounts() -> tuple[str, ...]:
    return _disk_mounts(int(time.monotonic() / 5))
