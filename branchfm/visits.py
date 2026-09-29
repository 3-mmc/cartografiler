"""Where you actually go. Branch counts the places you enter or open, so frequent routes
wear into roads, like desire paths across grass. Stored locally; never shared."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()


def _load(store: Path) -> dict:
    try:
        return json.loads(store.read_text())
    except (OSError, ValueError):
        return {}


def record(store: Path, path: str):
    with _lock:
        data = _load(store)
        count, _ = data.get(path, [0, 0])
        data[path] = [count+1, time.time()]
        if len(data) > 20000:  # forget the least-travelled routes first
            data = dict(sorted(data.items(), key=lambda item: (item[1][0], item[1][1]))[-15000:])
        store.parent.mkdir(parents=True, exist_ok=True)
        temporary = store.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        os.replace(temporary, store)


def counts(store: Path, paths: list[str]) -> dict:
    data = _load(store)
    return {p: data[p][0] for p in paths if p in data}
