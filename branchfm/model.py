from __future__ import annotations

import os
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def clean(value: object) -> str:
    """Never let filenames or file contents inject terminal control sequences."""
    return ''.join(c if c.isprintable() else '�' for c in str(value))


def size(n: int) -> str:
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if n < 1024 or unit == 'TiB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


@dataclass
class Node:
    path: Path
    parent: Node | None = None
    entries: list[Path] = field(default_factory=list)
    children: dict[Path, Node] = field(default_factory=dict)
    index: int = 0
    offset: int = 0
    query: str = ''
    error: str = ''
    x: int = 0
    y: int = 0

    @property
    def selected(self):
        return self.entries[self.index] if self.entries else None

    def refresh(self, hidden=False):
        old = self.selected
        try:
            self.entries = sorted(
                (p for p in self.path.iterdir()
                 if (hidden or not p.name.startswith('.')) and self.query.casefold() in p.name.casefold()),
                key=lambda p: (not p.is_dir(), p.name.casefold()))
            self.error = ''
        except OSError as e:
            self.entries, self.error = [], str(e)
        self.index = self.entries.index(old) if old in self.entries else min(self.index, max(0, len(self.entries)-1))
        self.children = {p: n for p, n in self.children.items() if p in self.entries and p.is_dir()}

    def visible(self, rows):
        self.offset = max(0, min(self.offset, len(self.entries)-rows))
        if self.index < self.offset:
            self.offset = self.index
        elif self.index >= self.offset+rows:
            self.offset = self.index-rows+1
        return self.entries[self.offset:self.offset+rows]


def layout(root: Node, rows=12, width=30, gap=8):
    """Allocate disjoint subtree bands; each expanded directory owns its listing."""
    nodes = []

    def place(node, depth, top):
        node.x, node.y = depth*(width+gap), top
        nodes.append(node)
        visible = node.visible(rows)
        end = top+max(1, len(visible))+3
        next_child = top
        for i, p in enumerate(visible):
            if p in node.children:
                child_top = max(top+i, next_child)
                next_child = place(node.children[p], depth+1, child_top)+2
                end = max(end, next_child)
        return end

    place(root, 0, 0)
    return nodes


class Operations:
    """Explicit operations; never overwrite destinations. Undo relocations only."""
    def __init__(self, trash: Path):
        self.trash = trash
        self.history = []

    def destination(self, source: Path, dest: Path):
        if os.path.lexists(dest):
            raise ValueError('Destination already exists; nothing was overwritten.')
        if source.is_dir() and not source.is_symlink() and dest.resolve().is_relative_to(source.resolve()):
            raise ValueError('Cannot put a directory inside itself.')
        if not dest.parent.is_dir():
            raise ValueError('Destination folder does not exist.')

    def move(self, source, dest):
        self.destination(source, dest)
        shutil.move(str(source), str(dest))
        self.history.append((dest, source))

    def copy(self, source, dest):
        self.destination(source, dest)
        if source.is_symlink():
            dest.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
        elif source.is_dir():
            shutil.copytree(source, dest, symlinks=True)
        elif stat.S_ISREG(source.stat().st_mode):
            # Exclusive creation also protects against a destination appearing meanwhile.
            with source.open('rb') as src, dest.open('xb') as out:
                shutil.copyfileobj(src, out)
            shutil.copystat(source, dest)
        else:
            raise ValueError('Copy supports regular files, directories, and symlinks.')

    def remove(self, source):
        self.trash.mkdir(parents=True, exist_ok=True, mode=0o700)
        dest = self.trash / (uuid.uuid4().hex+'--'+source.name)
        self.move(source, dest)
        # Recovery survives an application restart, even though the undo stack does not.
        with (self.trash/'recovery.tsv').open('a') as f:
            import json
            f.write(json.dumps({'stored': str(dest), 'original': str(source)})+'\n')

    def undo(self):
        if not self.history:
            raise ValueError('No move, rename, or trash operation to undo this session.')
        source, dest = self.history[-1]
        self.destination(source, dest)
        shutil.move(str(source), str(dest))
        self.history.pop()
