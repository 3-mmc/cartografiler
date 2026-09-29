"""The world map as a service: tiles, map labels, picking and survey status.

One Atlas owns the survey index, the lazily laid-out world, the terrain synthesiser and a
background surveyor. The native client streams tiles for whatever it is looking at and
polls /status, which folds survey progress into the map and lists regions to redraw.
"""
from __future__ import annotations

import json
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
from .world import HOME, LAYOUT_VERSION, SEA, World, climate, label_at

FULL_SURVEY_EVERY = 12*3600
TILE_CACHE_MAX_AGE = 24*3600    # colour fades with a folder's age in days; a day's drift is invisible


def _codec():
    """zstd where it is installed, deflate otherwise. On real tiles zstd-3 stores a third
    against deflate's 45% and is quicker both ways, and every cache hit pays the read."""
    try:
        import zstandard
    except ImportError:
        import zlib
        return b'D', lambda d: zlib.compress(d, 1), zlib.decompress
    c = zstandard.ZstdCompressor(level=3)
    d = zstandard.ZstdDecompressor()
    return b'Z', c.compress, d.decompress


TILE_CODEC, _compress, _decompress = _codec()
_DECOMPRESS = {}


def _expand(blob: bytes):
    """A tile written by whichever codec was installed then. A row this build cannot read is
    simply a miss, so a machine that loses zstd redraws rather than misreads."""
    tag, body = blob[:1], blob[1:]
    if tag == TILE_CODEC:
        return _decompress(body)
    if tag == b'D':
        import zlib
        return zlib.decompress(body)
    if tag == b'Z':
        import zstandard
        return zstandard.ZstdDecompressor().decompress(body)
    raise ValueError('unknown tile codec')
TILE_CACHE_TRANSIENT = 900      # a geyser's own window: a tile holding one may not outlive it


class TileStore:
    """Rendered tiles on disk, kept across sessions beside the laid-out territories.

    A tile costs 0.1–1.6 s to render and compresses to about a third, so a revisited place
    is worth reading back rather than drawing again. Validity is not a signature: a
    territory's signature is its shape (`World._signature`), and the raster also bakes in
    activity, which the shape never notices. Instead every tile is stored with the moment it
    stops being true, and dropped by the same bounding boxes that already invalidate the
    tiles held in memory. A tile showing a geyser expires with the geyser's 15-minute window;
    coarse tiles nearly always show one, since a child's recency is its whole subtree's."""
    def __init__(self, location):
        import sqlite3
        self.location = location
        self.local = threading.local()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tiles(level INTEGER, x INTEGER, y INTEGER, expires REAL, blob BLOB, PRIMARY KEY(level, x, y))')
            if 'expires' not in {r[1] for r in db.execute('PRAGMA table_info(tiles)')}:
                db.execute('DROP TABLE tiles')   # an older store keyed by when it was written
                db.execute('CREATE TABLE tiles(level INTEGER, x INTEGER, y INTEGER, expires REAL, blob BLOB, PRIMARY KEY(level, x, y))')
            db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)')
            row = db.execute("SELECT value FROM meta WHERE key='layout_version'").fetchone()
            if row is None or int(row[0]) != LAYOUT_VERSION:
                # The world is laid out differently now; every stored raster is of another map.
                db.execute('DELETE FROM tiles')
                db.execute("INSERT OR REPLACE INTO meta VALUES('layout_version', ?)", (str(LAYOUT_VERSION),))

    def db(self):
        import sqlite3
        db = getattr(self.local, 'db', None)
        if db is None:
            db = sqlite3.connect(self.location, timeout=30, check_same_thread=False)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=NORMAL')
            self.local.db = db
        return db

    def close(self):
        db = getattr(self.local, 'db', None)
        if db is not None:
            db.close()
            self.local.db = None

    def get(self, level, x, y):
        try:
            row = self.db().execute('SELECT expires, blob FROM tiles WHERE level=? AND x=? AND y=?', (level, x, y)).fetchone()
            if row is None or row[0] <= time.time():
                return None
            return _expand(row[1])
        except Exception:
            return None   # a cache: an unreadable row only costs a redraw

    def put(self, level, x, y, data, ttl=TILE_CACHE_MAX_AGE):
        try:
            db = self.db()
            db.execute('INSERT OR REPLACE INTO tiles VALUES(?,?,?,?,?)',
                       (level, x, y, time.time()+ttl, TILE_CODEC+_compress(data)))
            db.commit()
        except Exception:
            pass   # a cache: losing a write only costs a redraw

    def drop(self, boxes):
        """Forget every stored tile a changed region touches."""
        try:
            db = self.db()
            for level, x, y in [r for r in db.execute('SELECT level, x, y FROM tiles')
                                if any(_hit(_tile_box(*r), b) for b in boxes)]:
                db.execute('DELETE FROM tiles WHERE level=? AND x=? AND y=?', (level, x, y))
            db.commit()
        except Exception:
            pass


def _tile_box(level, x, y):
    S = 0.5**level
    return (x*S, y*S, (x+1)*S, (y+1)*S)


@lru_cache(maxsize=65536)
def zone_for(path: str):
    from .service import filesystem
    try:
        fs = filesystem(Path(path)) if os.path.exists(path) else {'zone': 'native', 'writable': True}
    except OSError:
        fs = {'zone': 'native', 'writable': True}
    return fs['zone'], fs.get('writable', True)


PLURAL = {'pdf': 'PDFs', 'images': 'images', 'video': 'videos', 'audio': 'recordings', 'tables': 'tables',
          'code': 'source files', 'databases': 'databases', 'archives': 'archives', 'binaries': 'executables',
          'disks': 'disk images', 'documents': 'documents', 'other': 'other files', 'weights': 'model weights',
          'industry': 'build outputs and models'}


LIBRARY = {'video': 'video library', 'images': 'photo library', 'audio': 'music library', 'pdf': 'PDF library',
           'documents': 'documents', 'code': 'source code', 'archives': 'archives', 'tables': 'tables',
           'databases': 'databases', 'disks': 'disk images', 'binaries': 'programs', 'weights': 'AI models'}


def subtitle_for(c: dict) -> str:
    """What a place is mostly made of, when that is clear: 'video library · 55 videos'."""
    from .world import dominant_kind
    kind, share = dominant_kind(c.get('kinds') or {}, c.get('kind_bytes') or {})
    n = (c.get('kinds') or {}).get(kind, 0)
    if kind is None or kind in ('other', 'folders') or share < 0.5 or n < 8:
        return ''
    return f"{LIBRARY.get(kind, kind)} · {patch_name(kind, n)}"


def patch_name(kind: str, n: int) -> str:
    return f"{n:,} {PLURAL.get(kind, 'files')}"


def climate_for(path: str) -> str:
    zone, writable = zone_for(path)
    return climate(zone, writable)


# ---------------------------------------------------------------- tile workers

_worker = {}


def _worker_init(index_path: str):
    # Drawing a tile is worth a second; dropping the window to 15 fps for it is not. The
    # workers fill every core while the map is still coming in, which is exactly when the
    # renderer needs one, so they yield to it.
    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass
    _worker['index'] = Index(index_path)
    _worker['serial'] = None


def _worker_tile(level: int, x: int, y: int, serial: int) -> tuple[bytes, bool]:
    # Each worker keeps its own lazily laid-out world, and starts afresh when the map changed.
    if _worker.get('serial') != serial:
        _worker['world'] = World(_worker['index'], zone_for=zone_for)
        _worker['synth'] = Synth(_worker['world'], zone_for=climate_for)
        _worker['serial'] = serial
    tile = _worker['synth'].tile(level, x, y)
    return encode(tile), bool(tile.get('transient'))


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
        loc = str(self.index.location) if getattr(self.index, 'location', None) is not None else ''
        self.store = TileStore(loc.replace('index.sqlite', 'tiles.sqlite') if loc.endswith('index.sqlite') else loc+'.tiles') if loc else None
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
            threading.Thread(target=self.find_monuments, daemon=True, name='monuments').start()

    # ---------------------------------------------------------------- tiles

    def tile(self, level: int, x: int, y: int) -> bytes:
        key = (level, x, y)
        with self.tile_lock:
            hit = self.tiles.get(key)
            if hit is not None:
                self.tiles.move_to_end(key)
                return hit[0]
        stored = self.store.get(level, x, y) if self.store else None
        if stored is not None:
            data, transient = stored, True      # already on disk; no need to write it back
        elif self.pool is not None:
            data, transient = self.pool.submit(_worker_tile, level, x, y, self.world.serial).result()
        else:
            with self.slots:
                tile = self.synth.tile(level, x, y)
                data, transient = encode(tile), bool(tile.get('transient'))
        if stored is None and self.store:
            self.store.put(level, x, y, data, TILE_CACHE_TRANSIENT if transient else TILE_CACHE_MAX_AGE)
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
                    if self.store:
                        self.store.drop(fresh)
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
        patches = []
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
                # Hidden folders (.git, .cache…) are named only when they are big on screen.
                hidden = '/.' in c['path']
                if side_px < (140 if hidden else 60):
                    continue
                kind = 'continent' if (t.continental and not c.get('same_land', True)) else 'region'
                regions.append({'name': c['name'], 'path': c['path'], 'x': c['centroid'][0], 'y': c['centroid'][1],
                                'side': c['side']*(0.4 if hidden else 1.0), 'depth': depth+1, 'kind': kind, 'scanned': c['scanned'],
                                'files': c['files'], 'dirs': c['dirs'], 'subtitle': subtitle_for(c)})
                if side_px >= 160:
                    inner = self.world.child(t, c)
                    if inner is not None:
                        stack.append((inner, depth+1))
            pl = t.places
            if pl.get('n'):
                # A patch is named as a whole ("412 images") until its parcels are big enough
                # to carry their own names.
                for q in pl.get('patches', []):
                    if q['n'] > 1 and q['side']/px >= 50 and x0 < q['x'] < x1 and y0 < q['y'] < y1 and not t.uniform:
                        patches.append({'name': patch_name(q['kind'], q['n']), 'x': q['x'], 'y': q['y'], 'side': q['side'],
                                        'kind': q['kind'], 'n': q['n'], 'path': t.path})
                r = pl['r']
                need = 36 if '/.' in t.path+'/' else 18
                primary = ~np.asarray(pl.get('companion', np.zeros(pl['n'], dtype=bool)))   # subtitles stay unnamed
                vis = np.flatnonzero((r/px >= need) & primary & (pl['x'] > x0) & (pl['x'] < x1) & (pl['y'] > y0) & (pl['y'] < y1))
                # A handful of names per patch (the largest files), more as parcels grow:
                # a field of 80 captions is one place, not 80 labels.
                if vis.size and 'patch' in pl:
                    keep = []
                    budget = int(np.clip(np.median(r[vis])/px/4, 6, 40))
                    for q in np.unique(pl['patch'][vis]):
                        members = vis[pl['patch'][vis] == q]
                        keep.extend(members[np.argsort(-pl['size'][members], kind='stable')][:budget])
                    vis = np.array(keep, dtype=np.int64)
                for i in vis[np.argsort(-r[vis])][:120]:
                    files.append({'name': pl['names'][i], 'path': pl['paths'][i], 'x': float(pl['x'][i]),
                                  'y': float(pl['y'][i]), 'r': float(r[i]), 'kind': pl['kinds'][i]})
        for m in self.monuments():
            if x0 < m['x'] < x1 and y0 < m['y'] < y1:
                # Named at every zoom, like the peak of a continent.
                files.append({'name': f"{m['name']} · largest file on {m['disk']}", 'path': m['path'], 'x': m['x'], 'y': m['y'],
                              'r': px*40, 'kind': 'monument'})
        regions.sort(key=lambda r: -r['side'])
        files.sort(key=lambda f: -f['r'])
        patches.sort(key=lambda q: -q['side'])
        return {'regions': regions[:200], 'files': files[:120], 'patches': patches[:60]}

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
                    if d[i] < max(pl['r'][i]*1.3, px*8):
                        found = {'name': pl['names'][i], 'path': pl['paths'][i], 'x': float(pl['x'][i]), 'y': float(pl['y'][i]),
                                 'r': float(pl['r'][i]), 'kind': pl['kinds'][i], 'role': (pl.get('roles') or [''] * pl['n'])[i]}
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

    def find_monuments(self, force: bool = False):
        """The largest file on each disk becomes its monument. A few seconds of scanning the
        index, so it runs in the background and is kept for a day in the index's meta table,
        where the tile workers read it."""
        try:
            db = self.index.db()
            row = db.execute("SELECT value FROM meta WHERE key='monuments'").fetchone()
            if row and not force and time.time()-json.loads(row[0]).get('time', 0) < 86400:
                return
            roots = sorted({World.continent_of(c['path']) for c in self.world.root().children})
            places = []
            for root in roots:
                if root == '/':
                    q = ("SELECT path, name, size FROM nodes WHERE is_dir=0 AND link=0 AND path NOT LIKE '/mnt/%' "
                         "AND path NOT LIKE '/proc/%' ORDER BY size DESC LIMIT 1")
                    hit = db.execute(q).fetchone()
                else:
                    hit = db.execute("SELECT path, name, size FROM nodes WHERE is_dir=0 AND link=0 AND path > ? AND path < ? "
                                     "ORDER BY size DESC LIMIT 1", (root+'/', root+'0')).fetchone()
                if not hit:
                    continue
                where = self.region(hit[0], survey=False)
                root_t = self.world.root()
                if root == '/':
                    disk_side = root_t.node.get('continent', {}).get('side', 0.2)
                else:
                    disk_side = next((c['side'] for c in root_t.children if c['path'] == root), 0.2)
                if 'x' in where:
                    places.append({'path': hit[0], 'name': hit[1], 'size': hit[2], 'x': where['x'], 'y': where['y'],
                                   'disk': World.display_name(root, root) if root != '/' else 'Linux', 'disk_side': disk_side})
            with self.index.write_lock:
                db.execute("INSERT OR REPLACE INTO meta VALUES('monuments', ?)", (json.dumps({'time': time.time(), 'places': places}),))
                db.commit()
        except Exception:
            pass   # a monument is a nicety; the map never waits for it

    def monuments(self) -> list:
        row = self.index.db().execute("SELECT value FROM meta WHERE key='monuments'").fetchone()
        return json.loads(row[0]).get('places', []) if row else []

    def region(self, path: str, survey: bool = True) -> dict:
        """Where a path is on the map (laying out its ancestors as needed)."""
        path = os.path.abspath(path)
        # Looking at a place refreshes its own listing first; its insides follow in survey order
        # if they have never been surveyed.
        target = path if os.path.isdir(path) else os.path.dirname(path)
        if survey:
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
            # Framed with its neighbours around it: a parcel is read in its patch.
            return {'path': path, 'name': pl['names'][i], 'x': float(pl['x'][i]), 'y': float(pl['y'][i]), 'side': r*6,
                    'bbox': (float(pl['x'][i])-r*3, float(pl['y'][i])-r*3, float(pl['x'][i])+r*3, float(pl['y'][i])+r*3),
                    'file': True, 'kind': pl['kinds'][i], 'r': r, 'role': (pl.get('roles') or [''] * pl['n'])[i]}
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
        if self.store:
            self.store.close()


def _hit(a, b) -> bool:
    return not (a[2] < b[0] or a[3] < b[1] or a[0] > b[2] or a[1] > b[3])
