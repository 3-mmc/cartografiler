"""Host services shared by the map and terminal companion.

World/index paths are still POSIX: Windows host support here does not yet make the
continuous world a native Windows application.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def godot_runtime(base: Path) -> Path:
    """Select a host runtime, or an explicitly configured executable."""
    override = os.environ.get('CARTOGRAFILER_GODOT')
    if override:
        executable = Path(override).expanduser()
        if executable.is_file():
            return executable
        raise ValueError('CARTOGRAFILER_GODOT must name an existing executable.')
    bundled = base/'runtime/CartografilerMap'
    if bundled.is_file():
        return bundled
    patterns = ('Godot*.app/Contents/MacOS/Godot',) if sys.platform == 'darwin' else (
        ('Godot*_win64*.exe',) if sys.platform == 'win32' else ('Godot*_linux.x86_64',))
    candidates = sorted(p for pattern in patterns for p in (base/'tools').glob(pattern))
    if not candidates:
        raise ValueError('Godot runtime missing for this platform. Set CARTOGRAFILER_GODOT; see README.md.')
    return candidates[-1]


def renderer_environment() -> dict[str, str]:
    env = dict(os.environ)
    if sys.platform.startswith('linux') and 'GALLIUM_DRIVER' not in env and Path('/usr/lib/wsl/lib/libd3d12.so').exists():
        env['GALLIUM_DRIVER'] = 'd3d12'
    return env


def open_external(path: Path) -> None:
    """Ask the host to open a path; never interpolate it into shell code."""
    if sys.platform == 'win32':
        os.startfile(str(path))
        return
    if sys.platform == 'darwin':
        cmd = ['/usr/bin/open', str(path)]
    elif str(path).startswith('/mnt/') and len(path.parts)>3 and len(path.parts[2])==1 and shutil.which('powershell.exe'):
        win = path.parts[2].upper()+':\\'+'\\'.join(path.parts[3:])
        encoded = base64.b64encode(win.encode()).decode()
        cmd = ['powershell.exe','-NoProfile','-NonInteractive','-Command',
               "$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"')); Invoke-Item -LiteralPath $p"]
    else:
        opener = shutil.which('xdg-open')
        if not opener:
            raise ValueError('No default opener found (xdg-open).')
        cmd = [opener, str(path)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def limit_worker_memory(limit: int) -> bool:
    """Apply Linux's address-space ceiling. Other hosts retain caller timeouts.

macOS does not provide a dependable equivalent here; Windows needs job objects.
Do not let an unavailable resource module turn every preview into an error.
"""
    if not sys.platform.startswith('linux'):
        return False
    import resource
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
    resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    return True


def parse_mount_table(text: str, *, macos: bool = False) -> list[tuple[str, str]]:
    table = []
    for line in text.splitlines():
        if macos:
            match = re.match(r'^.*? on (.*) \(([^,\)]+)(?:,.*)?\)$', line)
            if match:
                table.append((match[1], match[2]))
        else:
            parts = line.split()
            if len(parts) >= 3:
                path = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), parts[1])
                table.append((path, parts[2]))
    return table


def mount_table() -> list[tuple[str, str]]:
    try:
        if sys.platform == 'darwin':
            result = subprocess.run(['/sbin/mount'], capture_output=True, text=True, check=True, timeout=3)
            return parse_mount_table(result.stdout, macos=True)
        if sys.platform.startswith('linux'):
            return parse_mount_table(Path('/proc/mounts').read_text())
    except (OSError, subprocess.SubprocessError):
        pass
    return []  # Unknown stays unknown; native Windows volume indexing is pending.


def survey_exclusions() -> set[str]:
    """macOS exposes startup-volume internals a second time under this tree.

Users, Applications and private data are surveyed through their normal root paths.
External disks under /Volumes remain discoverable.
"""
    return {'/System/Volumes'} if sys.platform == 'darwin' else set()


def asset_root() -> Path:
    """Frozen resource directory, or the source checkout during development."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def tile_worker_count() -> int:
    """Reserve CPU capacity for the renderer; cap Mac worker memory and contention."""
    cores = os.cpu_count() or 4
    return max(1, min(4 if sys.platform == 'darwin' else 6, cores-2))
