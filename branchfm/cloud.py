"""Cloud-file hydration states (OneDrive, Proton Drive, any Cloud Files API provider).

WSL's 9p bridge does not expose Windows file attributes, so Windows is asked directly:
one PowerShell call lists the registered sync roots (cached for the session), and one
non-recursive call per directory reads the attribute bits. Nothing is hydrated: reading
attributes never downloads a cloud-only file.

Tide states, from the attribute bits:
  cloud      RECALL_ON_DATA_ACCESS / OFFLINE  → phantom island (charted, not present)
  pinned     PINNED ("always keep on this device") → land behind a dike
  local      downloaded, not pinned → tidal flat (Windows may reclaim it)
"""
from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path, PurePosixPath

RECALL_ON_DATA_ACCESS = 0x400000
RECALL_ON_OPEN = 0x40000
OFFLINE = 0x1000
PINNED = 0x80000
UNPINNED = 0x100000

_roots: list[tuple[str, str]] | None = None


def _powershell(script: str, timeout: float = 15) -> str:
    if not shutil.which('powershell.exe'):
        return ''
    # -EncodedCommand avoids every quoting layer between Bash and PowerShell.
    encoded = base64.b64encode(("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"+script).encode('utf-16-le')).decode()
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                                capture_output=True,timeout=timeout,cwd='/')
    except (OSError,subprocess.TimeoutExpired):
        return ''
    return result.stdout.decode('utf-8','replace').replace('\r','')


def to_windows(path: Path) -> str | None:
    parts = PurePosixPath(path).parts
    if len(parts)>=3 and parts[1]=='mnt' and len(parts[2])==1:
        return parts[2].upper()+':\\'+'\\'.join(parts[3:])
    return None


def to_linux(path: str) -> str:
    return '/mnt/'+path[0].lower()+'/'+path[3:].replace('\\','/') if len(path)>2 and path[1]==':' else path


def sync_roots() -> list[tuple[str, str]]:
    """(linux path, provider) for every registered cloud sync root."""
    global _roots
    if _roots is None:
        out = _powershell(r'''Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager" -ErrorAction SilentlyContinue | ForEach-Object { $n=$_.PSChildName; Get-ChildItem $_.PSPath -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -eq "UserSyncRoots" } | ForEach-Object { $k=$_; $k.GetValueNames() | ForEach-Object { $n.Split("!")[0] + "`t" + $k.GetValue($_) } } }''')
        _roots = []
        for line in out.splitlines():
            provider,_,root = line.partition('\t')
            if root:
                _roots.append((to_linux(root.strip()).rstrip('/'),provider.strip()))
    return _roots


def sync_root(path: Path) -> tuple[str, str] | None:
    text = str(path)
    for root,provider in sync_roots():
        if text==root or text.startswith(root+'/'):
            return root,provider
    return None


def tide(attributes: int) -> str:
    if attributes & (RECALL_ON_DATA_ACCESS|OFFLINE):
        return 'cloud'
    if attributes & PINNED:
        return 'pinned'
    return 'local'


def directory_tides(path: Path) -> dict:
    """{'provider', 'root', 'entries': {name: tide}} for a directory inside a sync root."""
    found = sync_root(path)
    windows = to_windows(path)
    if not found or not windows:
        return {}
    literal = windows.replace("'","''")
    out = _powershell(f"Get-ChildItem -LiteralPath '{literal}' -Force -ErrorAction SilentlyContinue | ForEach-Object {{ \"{{0}}`t{{1}}\" -f [int]$_.Attributes, $_.Name }}")
    entries = {}
    for line in out.splitlines():
        value,_,name = line.partition('\t')
        try:
            entries[name] = tide(int(value))
        except ValueError:
            continue
    return {'provider':found[1],'root':found[0],'entries':entries}
