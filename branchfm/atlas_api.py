"""The world map as a service: tiles, map labels, picking and survey status.

One Atlas owns the survey index, the lazily laid-out world, the terrain synthesiser and a
background surveyor. The native client streams tiles for whatever it is looking at and
polls /status, which folds survey progress into the map and lists regions to redraw.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import numpy as np

from .index import Index, Surveyor
from .tiles import N, Synth, encode
from .world import HOME, SEA, World, label_at

FULL_SURVEY_EVERY = 12*3600


@lru_cache(maxsize=65536)
def zone_for(path: str):
    from .service import filesystem
    try:
        fs = filesystem(Path(path)) if os.path.exists(path) else {'zone': 'native', 'writable': True}
    except OSError:
        fs = {'zone': 'native', 'writable': True}
    return fs['zone'], fs.get('writable', True)


def climate_for(path: str) -> str:
    zone, writable = zone_for(path)
    return zone if writable else 'alpine'


# ---------------------------------------------------------------- tile workers

_worker = {}


def _worker_init(index_path: str):
    _worker['index'] = Index(index_path)
    _worker['serial'] = None


def _worker_tile(level: int, x: int, y: int, serial: int) -> bytes:
    # Each worker keeps its own lazily laid-out world, and starts afresh when the map changed.
    if _worker.get('serial') != serial:
        _worker['world'] = World(_worker['index'], zone_for=zone_for)
        _worker['synth'] = Synth(_worker['world'], zone_for=climate_for)
        _worker['serial'] = serial
    return encode(_worker['synth'].tile(level, x, y))


class Atlas:
    def __init__(self, start: str, index: Index | None = None, survey: bool = True, workers: int | None = None):
        self.index = index or Index()
        self.world = World(self.index, zone_for=zone_for)
        self.synth = Synth(self.world, zone_for=climate_for)
        # Tiles render in worker processes, so picking, labels and status stay responsive.
        import concurrent.futures
        import multiprocessing
        count = workers if workers is not None else max(2, min(6, (os.cpu_count() or 4)-2))
        self.pool = concurrent.futures.ProcessPoolExecutor(count, mp_context=multiprocessing.get_context('spawn'),
                                                           initializer=_worker_init, initargs=(str(self.index.location),)) if count else None
        self.full_survey = False
        self.tiles: OrderedDict[tuple, tuple] = OrderedDict()
        self.tile_lock = threading.Lock()
        self.slots = threading.Semaphore(max(2, (os.cpu_count() or 4)//2))
        self.refresh_lock = threading.Lock()
        self.last_refresh = 0.0
        self.surveyor = Surveyor(self.index)
        if survey:
            self.surveyor.start()
            # Refresh where you start; the full survey (below) keeps the rest current.
            self.surveyor.request(start, 0, recursive=False)
            last = self.index.db().execute("SELECT value FROM meta WHERE key='last_full_survey'").fetchone()
            if last is None or time.time()-float(last[0]) > FULL_SURVEY_EVERY:
                self.surveyor.request('/', 3)
                self.full_survey = True

    # ---------------------------------------------------------------- tiles

    def tile(self, level: int, x: int, y: int) -> bytes:
        key = (level, x, y)
        with self.tile_lock:
            hit = self.tiles.get(key)
            if hit is not None:
                self.tiles.move_to_end(key)
                return hit[0]
        if self.pool is not None:
            data = self.pool.submit(_worker_tile, level, x, y, self.world.serial).result()
        else:
            with self.slots:
                data = encode(self.synth.tile(level, x, y))
        S = 0.5**level
        with self.tile_lock:
            self.tiles[key] = (data, (x*S, y*S, (x+1)*S, (y+1)*S))
            while len(self.tiles) > 1500:
                self.tiles.popitem(last=False)
        return data

    # ---------------------------------------------------------------- status

    def status(self, since: int) -> dict:
        with self.refresh_lock:
            if time.monotonic()-self.last_refresh > 1.5:
                self.last_refresh = time.monotonic()
                self.world.refresh()
                fresh = [b for s, b in self.world.invalid if s > getattr(self, 'dropped_serial', 0)]
                if fresh:
                    with self.tile_lock:
                        for key in [k for k, (_, bb) in self.tiles.items() if any(_hit(bb, b) for b in fresh)]:
                            del self.tiles[key]
                    self.dropped_serial = self.world.serial
        if self.full_survey and not self.surveyor.pending() and not self.surveyor.current:
            self.full_survey = False
            with self.index.write_lock:
                self.index.db().execute("INSERT OR REPLACE INTO meta VALUES('last_full_survey', ?)", (str(time.time()),))
                self.index.db().commit()
        stats = self.index.stats()
        invalid = [b for s, b in self.world.invalid if s > since]
        return {'serial': self.world.serial, 'invalid': invalid[-400:], 'files': stats.get('files', 0),
                'dirs': stats.get('dirs', 0), 'unscanned': stats.get('unscanned', 0),
                'queued': self.surveyor.pending(), 'current': self.surveyor.current}

    # ---------------------------------------------------------------- places

    def places(self, x0: float, y0: float, x1: float, y1: float, px: float) -> dict:
        """Names to print on the map: regions at least ~50 px across, files at least ~6 px."""
        regions = []
        files = []
        root = self.world.root()
        if root.node.get('continent'):
            c = root.node['continent']
            if c['side']/px >= 50:
                regions.append({'name': c['name'], 'path': '/', 'x': c['centroid'][0], 'y': c['centroid'][1],
                                'side': c['side'], 'depth': 0, 'kind': 'continent'})
        stack = [(root, 0)]
        visited = 0
        while stack and visited < 400:
            t, depth = stack.pop()
            visited += 1
            for c in t.children:
                bx0, by0, bx1, by1 = c['bbox']
                if bx1 < x0 or by1 < y0 or bx0 > x1 or by0 > y1:
                    continue
                side_px = c['side']/px
                if side_px < 50:
                    continue
                kind = 'continent' if (t.continental and not c.get('same_land', True)) else 'region'
                regions.append({'name': c['name'], 'path': c['path'], 'x': c['centroid'][0], 'y': c['centroid'][1],
                                'side': c['side'], 'depth': depth+1, 'kind': kind, 'scanned': c['scanned'],
                                'files': c['files'], 'dirs': c['dirs']})
                if side_px >= 160:
                    inner = self.world.child(t, c)
                    if inner is not None:
                        stack.append((inner, depth+1))
            pl = t.places
            if pl.get('n'):
                r = pl['r']
                vis = np.flatnonzero((r/px >= 6) & (pl['x'] > x0) & (pl['x'] < x1) & (pl['y'] > y0) & (pl['y'] < y1))
                for i in vis[np.argsort(-r[vis])][:300]:
                    files.append({'name': pl['names'][i], 'path': pl['paths'][i], 'x': float(pl['x'][i]),
                                  'y': float(pl['y'][i]), 'r': float(r[i]), 'kind': pl['kinds'][i]})
        regions.sort(key=lambda r: -r['side'])
        files.sort(key=lambda f: -f['r'])
        return {'regions': regions[:250], 'files': files[:300]}

    def at(self, x: float, y: float, px: float) -> dict:
        """What is under a point: the chain of places, and the file landmark if one is hit."""
        chain = []
        found = None
        t = self.world.root()
        xs = np.array([x])
        ys = np.array([y])
        for _ in range(64):
            lab, _ = label_at(t, xs, ys, px)
            lab = int(lab[0])
            if lab == HOME or lab == 0:
                pl = t.places
                if pl.get('n'):
                    d = np.hypot(pl['x']-x, pl['y']-y)
                    i = int(np.argmin(d))
                    if d[i] < max(pl['r'][i]*1.4, px*8):
                        found = {'name': pl['names'][i], 'path': pl['paths'][i], 'x': float(pl['x'][i]), 'y': float(pl['y'][i]),
                                 'r': float(pl['r'][i]), 'kind': pl['kinds'][i]}
                break
            entry = t.child_by_label.get(lab)
            if entry is None:
                break
            chain.append(self._entry(entry))
            if entry['side'] < px*10:
                break
            inner = self.world.child(t, entry)
            if inner is None:
                break
            t = inner
        return {'chain': chain, 'file': found, 'sea': bool(chain == [] and found is None)}

    def region(self, path: str) -> dict:
        """Where a path is on the map (laying out its ancestors as needed)."""
        path = os.path.abspath(path)
        # Looking at a place refreshes its own listing first; its insides follow in survey order
        # if they have never been surveyed.
        target = path if os.path.isdir(path) else os.path.dirname(path)
        node = self.index.node(target)
        self.surveyor.request(target, 0, recursive=node is None or not node['scanned'])
        if path == '/':
            return {'path': '/', 'name': '/', 'x': 0.5, 'y': 0.5, 'side': 0.8, 'bbox': (0.1, 0.1, 0.9, 0.9)}
        parent = self.world.territory_for(os.path.dirname(path))
        if parent is None:
            return {'error': 'Not surveyed yet. The survey has been asked to go there first.'}
        entry = next((c for c in parent.children if c['path'] == path), None)
        if entry is not None:
            return self._entry(entry)
        pl = parent.places
        if pl.get('n') and path in pl['paths']:
            i = pl['paths'].index(path)
            r = float(pl['r'][i])
            return {'path': path, 'name': pl['names'][i], 'x': float(pl['x'][i]), 'y': float(pl['y'][i]), 'side': r*2,
                    'bbox': (float(pl['x'][i])-r, float(pl['y'][i])-r, float(pl['x'][i])+r, float(pl['y'][i])+r), 'file': True}
        return {'error': 'Not on the map yet. The survey has been asked to go there first.'}

    @staticmethod
    def _entry(c: dict) -> dict:
        return {'path': c['path'], 'name': c['name'], 'x': c['centroid'][0], 'y': c['centroid'][1], 'side': c['side'],
                'bbox': c['bbox'], 'files': c['files'], 'dirs': c['dirs'], 'scanned': c['scanned'], 'day': c['day'],
                'week': c['week'], 'newest': c['newest'], 'kinds': c['kinds']}

    def close(self):
        self.surveyor.stop()
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)


def _hit(a, b) -> bool:
    return not (a[2] < b[0] or a[3] < b[1] or a[0] > b[2] or a[1] > b[3])
