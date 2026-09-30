"""Data disks mounted in the conventional Linux/WSL mount directories."""
from functools import lru_cache
from pathlib import Path
import re
import time


def parse_disk_mounts(text: str) -> tuple[str, ...]:
    roots = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        path = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[1])
        kind = fields[2]
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
    try:
        return parse_disk_mounts(Path('/proc/mounts').read_text())
    except OSError:
        return ()


def disk_mounts() -> tuple[str, ...]:
    return _disk_mounts(int(time.monotonic() / 5))
