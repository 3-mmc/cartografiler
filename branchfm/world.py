"""World layout: every directory owns a territory on one continuous map.

The filesystem root is the whole world. A directory's territory is divided among its
subdirectories and a home district, where its own files stand as landmarks. The division
is a weighted Voronoi (power diagram) solved on a raster, so area follows subtree size. It
is deterministic: seeds come from path hashes, not from sibling order, so a growing index
shifts borders gently instead of reshuffling the map.

Borders are refined continuously. At any resolution a point's owner is decided by
interpolating the raster's labels with fractal noise, so coastlines and provincial borders
gain detail as you zoom, and a child's own raster is sampled through the same function
(one consistent world).

Continents: the root, directories holding several mount points, and large directories
with many subdirectories split their children across open water. Everywhere else siblings
share land borders along ridgelines, and rivers drain each territory toward its parent.

Territories are computed lazily, when the map first needs a place's interior.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

OUT, HOME, SEA = 0, 1, 2
FIRST_CHILD = 3
MAX_CHILDREN = 3000

# ---------------------------------------------------------------- hashing & noise

def stable_hash(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode('utf-8', 'surrogatepass'), digest_size=8).digest(), 'little')


M1 = np.uint64(0x9E3779B97F4A7C15)
M2 = np.uint64(0xBF58476D1CE4E5B9)
M3 = np.uint64(0x94D049BB133111EB)


def _mix(h):
    h = (h ^ (h >> np.uint64(30))) * M2
    h = (h ^ (h >> np.uint64(27))) * M3
    return h ^ (h >> np.uint64(31))


def lattice(i, j, seed: int):
    """Hash of integer lattice points to [0, 1)."""
    with np.errstate(over='ignore'):
        s = np.asarray(seed, dtype=np.int64).view(np.uint64) & np.uint64(0xFFFFFFFFFFFF)
        h = i.astype(np.int64).view(np.uint64)*M1 ^ _mix(j.astype(np.int64).view(np.uint64) + s*M3)
        h = _mix(h)
    return (h >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def value_noise(x, y, freq: float, seed):
    X = x*freq
    Y = y*freq
    xi = np.floor(X)
    yi = np.floor(Y)
    fx = X-xi
    fy = Y-yi
    fx = fx*fx*(3-2*fx)
    fy = fy*fy*(3-2*fy)
    xi = xi.astype(np.int64)
    yi = yi.astype(np.int64)
    a = lattice(xi, yi, seed)
    b = lattice(xi+1, yi, seed)
    c = lattice(xi, yi+1, seed)
    d = lattice(xi+1, yi+1, seed)
    return (a + (b-a)*fx) + ((c + (d-c)*fx) - (a + (b-a)*fx))*fy


def fbm(x, y, freq: float, octaves: int, seed, gain: float = 0.5):
    """Fractal noise in [-1, 1] (roughly), deterministic in world coordinates."""
    total = np.zeros_like(x, dtype=np.float64)
    amp = 1.0
    norm = 0.0
    for o in range(max(1, octaves)):
        total += (value_noise(x, y, freq*(2.03**o), seed+o*1013)*2-1)*amp
        norm += amp
        amp *= gain
    return total/norm


class GridNoise:
    """Noise on a regular pixel grid, identical to value_noise/fbm at the same world points
    but far cheaper: only the lattice points the grid spans are hashed, then gathered."""
    def __init__(self, x0: float, y0: float, px: float, n: int):
        self.px = px
        self.gx = x0+np.arange(n)*px
        self.gy = y0+np.arange(n)*px
        self.memo = {}

    def value(self, freq: float, seed: int):
        # Detail finer than two pixels is invisible and would hash enormous lattices.
        freq = min(freq, 0.5/self.px)
        key = ('v', freq, seed)
        hit = self.memo.get(key)
        if hit is not None:
            return hit
        X = self.gx*freq
        Y = self.gy*freq
        ix = np.floor(X)
        iy = np.floor(Y)
        fx = X-ix
        fy = Y-iy
        fx = fx*fx*(3-2*fx)
        fy = fy*fy*(3-2*fy)
        ix = ix.astype(np.int64)
        iy = iy.astype(np.int64)
        i0, j0 = int(ix.min()), int(iy.min())
        I, J = np.meshgrid(np.arange(i0, int(ix.max())+2), np.arange(j0, int(iy.max())+2))
        L = lattice(I, J, seed)
        cx = ix-i0
        cy = iy-j0
        a = L[np.ix_(cy, cx)]
        b = L[np.ix_(cy, cx+1)]
        c = L[np.ix_(cy+1, cx)]
        d = L[np.ix_(cy+1, cx+1)]
        top = a+(b-a)*fx[None, :]
        bottom = c+(d-c)*fx[None, :]
        out = (top+(bottom-top)*fy[:, None]).ravel()
        self.memo[key] = out
        return out

    def fbm(self, freq: float, octaves: int, seed: int, gain: float = 0.5):
        key = ('f', freq, octaves, seed, gain)
        hit = self.memo.get(key)
        if hit is not None:
            return hit
        total = np.zeros(self.gx.size*self.gy.size)
        amp = 1.0
        norm = 0.0
        for o in range(max(1, octaves)):
            total += (self.value(freq*(2.03**o), seed+o*1013)*2-1)*amp
            norm += amp
            amp *= gain
        total /= norm
        self.memo[key] = total
        return total


# ---------------------------------------------------------------- territory

@dataclass
class Territory:
    node_id: int
    path: str
    name: str
    depth: int
    x0: float
    y0: float
    size: float
    n: int
    labels: np.ndarray
    border: np.ndarray
    children: list
    capital: tuple
    outlet: tuple | None
    continental: bool
    zone: str
    writable: bool
    rivers: list
    places: dict
    node: dict
    sig: str
    version: int
    child_by_label: dict = field(default_factory=dict)
    sea_dist: np.ndarray | None = None

    @property
    def cell(self) -> float:
        return self.size/self.n


WARP_ROUGHNESS = 0.8
WARP_NORM = 1.0/(1.0-2.0**-WARP_ROUGHNESS)


def warp_offsets(xs, ys, wl: float, amp: float, px: float, grid: 'GridNoise | None' = None, idx=None):
    """Domain-warp displacement from one shared stack of world-absolute octaves (1/2^j).

    Every territory uses the same fields and only chooses its band (wavelength wl down to two
    pixels) and amplitude, so a tile computes each octave once for all territories, and the
    point-based evaluation used at layout time is exactly the same function."""
    j0 = int(math.ceil(math.log2(1.0/wl)))
    j1 = int(math.floor(math.log2(1.0/max(px*2, 1e-300))))
    dx = np.zeros(xs.shape)
    dy = np.zeros(xs.shape)
    for j in range(j0, max(j0, j1)+1):
        freq = 2.0**j
        weight = (1.0/(freq*wl))**WARP_ROUGHNESS
        if grid is not None:
            vx = grid.value(freq, 90000+2*j)[idx]
            vy = grid.value(freq, 90001+2*j)[idx]
        else:
            vx = value_noise(xs, ys, freq, 90000+2*j)
            vy = value_noise(xs, ys, freq, 90001+2*j)
        dx += (vx*2-1)*weight
        dy += (vy*2-1)*weight
    scale = amp/WARP_NORM
    return dx*scale, dy*scale


def warp_amplitude(t: Territory) -> float:
    return t.cell*3.0


def label_at(t: Territory, xs, ys, px: float, grid: 'GridNoise | None' = None, idx=None):
    """Owner label and distance to the nearest border (world units) at world points.

    Near borders the lookup point is displaced by a multi-octave domain warp, from ten
    cells down to two pixels, so borders and coastlines wander at every zoom like real
    ones. Far from any border the raster answers directly (most pixels, at any depth)."""
    n = t.n
    u = (xs-t.x0)/t.size*n-0.5
    v = (ys-t.y0)/t.size*n-0.5
    iu = np.clip(np.rint(u).astype(np.int64), 0, n-1)
    jv = np.clip(np.rint(v).astype(np.int64), 0, n-1)
    result = t.labels[jv, iu].copy()
    dist = _bilinear(t.border, u, v, n)*t.cell
    amp = warp_amplitude(t)
    near = dist < amp*1.6+t.cell
    if near.any():
        xm, ym = xs[near], ys[near]
        use_grid = grid is not None and idx is not None and near.sum() > 3000
        ox, oy = warp_offsets(xm, ym, t.cell*16, amp, px, grid if use_grid else None, idx[near] if use_grid else None)
        wx = xm+ox
        wy = ym+oy
        uu = (wx-t.x0)/t.size*n-0.5
        vv = (wy-t.y0)/t.size*n-0.5
        i0 = np.clip(np.floor(uu).astype(np.int64), 0, n-1)
        j0 = np.clip(np.floor(vv).astype(np.int64), 0, n-1)
        i1 = np.clip(i0+1, 0, n-1)
        j1 = np.clip(j0+1, 0, n-1)
        fu = np.clip(uu-np.floor(uu), 0, 1)
        fv = np.clip(vv-np.floor(vv), 0, 1)
        corners = [t.labels[j0, i0], t.labels[j0, i1], t.labels[j1, i0], t.labels[j1, i1]]
        weights = [(1-fu)*(1-fv), fu*(1-fv), (1-fu)*fv, fu*fv]
        best = np.full(xm.shape, -1.0)
        choice = corners[0].copy()
        for k in range(4):
            score = np.zeros(xm.shape)
            for m in range(4):
                score += weights[m]*(corners[m] == corners[k])
            better = score > best
            best = np.where(better, score, best)
            choice = np.where(better, corners[k], choice)
        result[near] = choice
        dist[near] = _bilinear(t.border, uu, vv, n)*t.cell
        outside = (uu < -0.5) | (vv < -0.5) | (uu > n-0.5) | (vv > n-0.5)
        sub = result[near]
        sub[outside] = OUT
        result[near] = sub
    far_out = (u < -0.5-3) | (v < -0.5-3) | (u > n+2.5) | (v > n+2.5)
    result[far_out] = OUT
    return result, dist


def sea_distance(t: Territory, xs, ys):
    """Distance to open water (world units); huge where a territory has no sea."""
    if t.sea_dist is None:
        return np.full(np.shape(xs), 1e300)
    u = (xs-t.x0)/t.size*t.n-0.5
    v = (ys-t.y0)/t.size*t.n-0.5
    return _bilinear(t.sea_dist, u, v, t.n)*t.cell


def _bilinear(grid, u, v, n):
    i0 = np.clip(np.floor(u).astype(np.int64), 0, n-1)
    j0 = np.clip(np.floor(v).astype(np.int64), 0, n-1)
    i1 = np.clip(i0+1, 0, n-1)
    j1 = np.clip(j0+1, 0, n-1)
    fu = np.clip(u-np.floor(u), 0, 1)
    fv = np.clip(v-np.floor(v), 0, 1)
    return (grid[j0, i0]*(1-fu)*(1-fv) + grid[j0, i1]*fu*(1-fv) + grid[j1, i0]*(1-fu)*fv + grid[j1, i1]*fu*fv)


def _cell_centres(x0, y0, size, n):
    c = (np.arange(n)+0.5)/n*size
    return np.meshgrid(x0+c, y0+c)


def _power_diagram(points, seeds, weights_target, iterations=14):
    """Capacity-balanced power diagram over raster points. Returns owner index per point."""
    k = len(seeds)
    if k == 1:
        return np.zeros(len(points), dtype=np.int64)
    seeds = seeds.astype(np.float64).copy()
    power = np.zeros(k)
    target = weights_target/weights_target.sum()*len(points)
    owner = np.zeros(len(points), dtype=np.int64)
    zeros = np.zeros((len(points), 1))
    query = np.hstack([points, zeros])
    for it in range(iterations):
        top = power.max()+1.0
        lifted = np.hstack([seeds, np.sqrt(top-power)[:, None]])
        _, owner = cKDTree(lifted).query(query, workers=1)
        area = np.bincount(owner, minlength=k).astype(np.float64)
        if it == iterations-1:
            break
        cx = np.bincount(owner, weights=points[:, 0], minlength=k)
        cy = np.bincount(owner, weights=points[:, 1], minlength=k)
        has = area > 0
        centroid = seeds.copy()
        centroid[has, 0] = cx[has]/area[has]
        centroid[has, 1] = cy[has]/area[has]
        seeds[has] += (centroid[has]-seeds[has])*0.55
        power += 0.7*(target-area)/math.pi
        empty = ~has
        if empty.any():
            big = int(np.argmax(area-target))
            members = np.flatnonzero(owner == big)
            for e in np.flatnonzero(empty):
                seeds[e] = points[members[(e*2654435761) % len(members)]]
                power[e] = power[big]
    return owner


def _boundary(labels):
    b = np.zeros(labels.shape, dtype=bool)
    b[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    b[:, :-1] |= labels[:, 1:] != labels[:, :-1]
    b[1:, :] |= labels[1:, :] != labels[:-1, :]
    b[:-1, :] |= labels[1:, :] != labels[:-1, :]
    return b


def _meander(a, b, seed, amount=0.18, points=14):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    d = b-a
    length = float(np.hypot(*d))
    if length == 0:
        return np.array([a, b])
    normal = np.array([-d[1], d[0]])/length
    t = np.linspace(0, 1, points)
    rng = np.random.default_rng(seed)
    phase = rng.uniform(0, math.tau, 3)
    swing = (np.sin(t*math.pi*2.3+phase[0])*0.6 + np.sin(t*math.pi*5.1+phase[1])*0.3 + np.sin(t*math.pi*9.7+phase[2])*0.12)
    swing *= np.sin(t*math.pi)*amount*length
    return a[None, :] + t[:, None]*d[None, :] + swing[:, None]*normal[None, :]


def weight_of(files, dirs, scanned=True):
    if not scanned and not (files or dirs):
        return 2.0
    # Compressed so a 300k-file tree doesn't swallow its 50-file siblings.
    w = (1.0+files+dirs)**0.4
    return math.exp(round(math.log(w)*3)/3)   # quantised: small index changes keep borders still


class LayoutStore:
    """Laid-out territories on disk, shared by every process and kept across sessions.
    Keyed by node and signature, so a territory whose inputs changed is simply recomputed."""
    def __init__(self, location):
        import sqlite3
        self.location = location
        self.local = threading.local()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS territories(node INTEGER PRIMARY KEY, sig TEXT, blob BLOB)')

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

    def get(self, node_id, sig):
        import pickle
        row = self.db().execute('SELECT blob FROM territories WHERE node=? AND sig=?', (node_id, sig)).fetchone()
        return pickle.loads(row[0]) if row else None

    def put(self, t):
        import pickle
        try:
            self.db().execute('INSERT OR REPLACE INTO territories VALUES(?,?,?)', (t.node_id, t.sig, pickle.dumps(t, protocol=5)))
            self.db().commit()
        except Exception:
            pass   # a cache: losing a write only costs a recomputation


class World:
    def __init__(self, index, zone_for=None, store: LayoutStore | None = None):
        self.index = index
        if store is None and getattr(index, 'location', None) is not None:
            store = LayoutStore(str(index.location).replace('index.sqlite', 'layout.sqlite') if str(index.location).endswith('index.sqlite') else str(index.location)+'.layout')
        self.store = store
        self.cache: OrderedDict[int, Territory] = OrderedDict()
        self.lock = threading.RLock()
        self.zone_for = zone_for or (lambda path: ('native', True))
        self.invalid = []   # (serial, bbox) for tiles that must be redrawn
        self.serial = 0
        self.seen_listed = 0.0
        self.seen_version = 0

    # ---------------------------------------------------------------- access

    def root(self) -> Territory:
        node = self.index.node('/')
        return self._get(dict(node), None, None)

    def child(self, parent: Territory, child: dict) -> Territory | None:
        if not child.get('scanned') and not (child.get('dirs') or child.get('files')):
            return None   # terra incognita: nothing surveyed inside yet
        node = self.index.node_by_id(child['id'])
        if node is None:
            return None
        return self._get(dict(node), parent, child)

    def territory_for(self, path: str) -> Territory | None:
        """Walk from the root to a directory's territory (computing ancestors as needed)."""
        t = self.root()
        if path == '/':
            return t
        parts = [p for p in path.split('/') if p]
        current = ''
        for part in parts:
            current += '/'+part
            match = next((c for c in t.children if c['path'] == current), None)
            if match is None:
                if t.path == '/':
                    continue   # /mnt is folded away between the world and its drives
                return None
            nxt = self.child(t, match)
            if nxt is None:
                return None
            t = nxt
        return t

    def _get(self, node: dict, parent: Territory | None, entry: dict | None) -> Territory:
        key = node['id']
        with self.lock:
            t = self.cache.get(key)
            if t is not None:
                # The cache is authoritative; refresh() folds survey progress into it.
                self.cache.move_to_end(key)
                return t
        rows = [dict(r) for r in self.index.children(node['id'])]
        if parent is None:
            rows = self._promote_mounts(rows)
        sig = self._signature(node, rows, parent, entry)
        with self.lock:
            if t is not None and t.sig == sig:
                t.version = self.index.version
                return t
        fresh = self.store.get(node['id'], sig) if self.store else None
        if fresh is None:
            fresh = self._lay_out(node, rows, parent, entry, sig)
            if self.store:
                self.store.put(fresh)
        else:
            # The cache key is the shape; statistics (survey state, counts, activity) are
            # always taken fresh from the index.
            self._refresh_stats(fresh, rows)
        fresh.version = self.index.version
        with self.lock:
            if t is not None and t.sig != fresh.sig:
                self._invalidate((fresh.x0, fresh.y0, fresh.x0+fresh.size, fresh.y0+fresh.size))
            self.cache[key] = fresh
            self.cache.move_to_end(key)
            while len(self.cache) > 3000:
                self.cache.popitem(last=False)
        return fresh

    def _promote_mounts(self, rows):
        # Continents are disks: drives mounted under /mnt join the world root directly,
        # and /mnt itself (a list of mount points) disappears from the map.
        out = []
        for r in rows:
            if r['is_dir'] and not r['link'] and r['path'] == '/mnt':
                for c in self.index.children(r['id']):
                    c = dict(c)
                    if c['is_dir'] and not c['link'] and os.path.ismount(c['path']) and c['path'] not in ('/mnt/wsl', '/mnt/wslg'):
                        out.append(c)
                continue
            out.append(r)
        return out

    @staticmethod
    def continent_of(path: str) -> str:
        parts = path.split('/')
        if len(parts) >= 3 and parts[1] == 'mnt' and len(parts[2]) == 1:
            return '/mnt/'+parts[2]
        return '/'

    @staticmethod
    def display_name(path: str, name: str) -> str:
        parts = path.split('/')
        if len(parts) == 3 and parts[1] == 'mnt' and len(parts[2]) == 1:
            return parts[2].upper()+':'
        return name

    @staticmethod
    def _refresh_stats(t, rows):
        by_id = {r['id']: r for r in rows}
        for c in t.children:
            r = by_id.get(c['id'])
            if r is None:
                continue
            for key in ('files', 'dirs', 'bytes', 'newest', 'day', 'week', 'unscanned', 'error', 'mtime'):
                c[key] = r[key]
            c['scanned'] = bool(r['scanned'])
            c['kinds'] = json.loads(r['kinds']) if r['kinds'] else {}

    def _invalidate(self, bbox):
        self.serial += 1
        self.invalid.append((self.serial, bbox))
        del self.invalid[:-4000]

    def refresh(self):
        """Fold survey progress into the cached map. Territories are re-laid out only when
        their shape would change; places whose own listings changed are redrawn."""
        listed = [(t, p) for t, p in list(self.index.listed) if t > self.seen_listed]
        if listed:
            self.seen_listed = listed[-1][0]
        fresh_paths = {p for _, p in listed}
        changed = {p for v, p in list(self.index.changed) if v > self.seen_version}
        self.seen_version = self.index.version
        with self.lock:
            cached = [t for t in self.cache.values() if t.path in changed or t.path in fresh_paths]
        for t in cached:
            node = self.index.node_by_id(t.node_id)
            if node is None:
                with self.lock:
                    self.cache.pop(t.node_id, None)
                continue
            rows = [dict(r) for r in self.index.children(t.node_id)]
            if t.path == '/':
                rows = self._promote_mounts(rows)
            by_id = {r['id']: r for r in rows}
            stale_shape = False
            for c in t.children:
                r = by_id.get(c['id'])
                if r is None:
                    stale_shape = True
                    continue
                if abs(math.log(weight_of(r['files'], r['dirs'], bool(r['scanned'])))-math.log(max(c['weight'], 1e-9))) > 0.35:
                    stale_shape = True   # grew or shrank enough that its borders should move
                for key in ('files', 'dirs', 'bytes', 'newest', 'day', 'week', 'unscanned', 'error'):
                    c[key] = r[key]
                c['scanned'] = bool(r['scanned'])
                c['kinds'] = json.loads(r['kinds']) if r['kinds'] else {}
            known = {c['id'] for c in t.children}
            if any(r['is_dir'] and not r['link'] and r['id'] not in known for r in rows):
                stale_shape = True
            if stale_shape:
                with self.lock:
                    self.cache.pop(t.node_id, None)
                    for other in list(self.cache.values()):
                        if other.path.startswith(t.path.rstrip('/')+'/'):
                            self.cache.pop(other.node_id, None)
                self._invalidate((t.x0, t.y0, t.x0+t.size, t.y0+t.size))
                continue
            t.version = self.index.version
            if t.path in fresh_paths:
                files = [r for r in rows if not (r['is_dir'] and not r['link'])]
                t.places = self._places(t, files, t.labels, t.cell)
                self._invalidate((t.x0, t.y0, t.x0+t.size, t.y0+t.size))
        return self.serial

    def _signature(self, node, rows, parent, entry):
        dirs = [(r['id'], round(math.log(weight_of(r['files'], r['dirs'], bool(r['scanned']))), 2)) for r in rows if r['is_dir'] and not r['link']]
        files = len(rows)-len(dirs)
        frame = (parent.sig, entry['label']) if parent is not None else ('root',)
        return hashlib.blake2b(repr((frame, dirs, files, bool(node['scanned']))).encode(), digest_size=12).hexdigest()

    # ---------------------------------------------------------------- layout

    def _lay_out(self, node, rows, parent, entry, sig) -> Territory:
        path = node['path']
        subdirs = [r for r in rows if r['is_dir'] and not r['link']]
        files = [r for r in rows if not (r['is_dir'] and not r['link'])]
        subdirs.sort(key=lambda r: -weight_of(r['files'], r['dirs'], bool(r['scanned'])))
        overflow = subdirs[MAX_CHILDREN:]
        subdirs = subdirs[:MAX_CHILDREN]
        k = len(subdirs)
        continental = parent is None   # seas separate disks only
        n = int(np.clip((20 if continental else 14)*math.sqrt(k+1), 64 if continental else 48, 360))

        # Frame and mask: the root is the whole world; a child is framed by its cells in
        # the parent and its mask is the parent's label field sampled at our resolution.
        if parent is None:
            # The world is an irregular landmass cluster in open ocean, not a filled square.
            x0, y0, size = 0.0, 0.0, 1.0
            gx, gy = _cell_centres(x0, y0, size, n)
            r = np.hypot(gx-0.5, gy-0.5)
            angle = np.arctan2(gy-0.5, gx-0.5)
            wobble = fbm(np.cos(angle)*0.5+0.5, np.sin(angle)*0.5+0.5, 3.0, 4, 4242)
            mask = r < 0.43*(1.0+0.16*wobble)
        else:
            bx0, by0, bx1, by1 = entry['bbox']
            pad = parent.cell*1.5
            side = max(bx1-bx0, by1-by0)+2*pad
            x0 = (bx0+bx1)/2-side/2
            y0 = (by0+by1)/2-side/2
            size = side
            gx, gy = _cell_centres(x0, y0, size, n)
            lab, _ = label_at(parent, gx.ravel(), gy.ravel(), size/n)
            mask = (lab == entry['label']).reshape(n, n)
            if not mask.any():
                mask[n//2, n//2] = True
        cell = size/n
        zone, writable = self.zone_for(path)

        # Seeds from path hashes: stable when siblings come and go.
        cells = np.column_stack([np.flatnonzero(mask.ravel()) % n, np.flatnonzero(mask.ravel()) // n]).astype(np.float64)+0.5
        outlet = entry.get('outlet') if entry else None
        if outlet is not None:
            ou = np.array([(outlet[0]-x0)/cell, (outlet[1]-y0)/cell])
        centre = cells.mean(axis=0)
        seeds = []
        weights = []
        if outlet is not None and not continental:
            home_seed = centre+(ou-centre)*0.55
        else:
            home_seed = centre
        home_seed = cells[np.argmin(((cells-home_seed)**2).sum(axis=1))]
        seeds.append(home_seed)
        weights.append(0.3+0.9*math.sqrt(len(files)))
        for r in subdirs:
            h = stable_hash(r['path'])
            seeds.append(cells[h % len(cells)] + np.array([((h >> 20) % 1000)/1000-0.5, ((h >> 30) % 1000)/1000-0.5]))
            weights.append(weight_of(r['files'], r['dirs'], bool(r['scanned'])))
        seeds = np.array(seeds)
        weights = np.array(weights)
        weights = np.maximum(weights, weights.max()*0.05)   # every place keeps visible land
        # Irregular borders: warp the raster before solving.
        # Large-scale warp before solving: territories bend and sprawl instead of forming polygons.
        wx = fbm(cells[:, 0]*cell+x0, cells[:, 1]*cell+y0, 1.0/(size/3), 4, stable_hash(path) & 0xFFFF)*n*0.10
        wy = fbm(cells[:, 0]*cell+x0, cells[:, 1]*cell+y0, 1.0/(size/3), 4, (stable_hash(path) >> 16) & 0xFFFF)*n*0.10
        warped = cells+np.column_stack([wx, wy])
        member_group = np.zeros(len(seeds), dtype=np.int64)
        if parent is None:
            # Two stages: first continents (one per disk), then provinces within each, so a
            # disk is always one connected landmass.
            keys = ['/']+sorted({self.continent_of(r['path']) for r in subdirs}-{'/'})
            member_group = np.array([0]+[keys.index(self.continent_of(r['path'])) for r in subdirs])
            group_weight = np.array([weights[member_group == g].sum() for g in range(len(keys))])
            group_seeds = np.array([cells[stable_hash('continent'+key) % len(cells)] for key in keys])
            group_owner = _power_diagram(warped, group_seeds, group_weight, iterations=24)
            owner = np.zeros(len(cells), dtype=np.int64)
            for g in range(len(keys)):
                region = group_owner == g
                members = np.flatnonzero(member_group == g)
                if not region.any() or len(members) == 0:
                    continue
                sub = cells[region]
                local_seeds = []
                for m in members:
                    if m == 0:
                        centre_g = sub.mean(axis=0)
                        local_seeds.append(sub[np.argmin(((sub-centre_g)**2).sum(axis=1))])
                    else:
                        h = stable_hash(subdirs[m-1]['path'])
                        local_seeds.append(sub[h % len(sub)])
                owner[region] = members[_power_diagram(warped[region], np.array(local_seeds), weights[members])]
        else:
            owner = _power_diagram(warped, seeds, weights)
        labels = np.zeros(n*n, dtype=np.uint16)
        flat = np.flatnonzero(mask.ravel())
        labels[flat] = np.where(owner == 0, HOME, owner-1+FIRST_CHILD)
        labels = labels.reshape(n, n)

        if continental:
            # Open water between continents (disks), with ragged coasts.
            group_grid = np.full(n*n, -1, dtype=np.int64)
            group_grid[flat] = member_group[owner]
            group_grid = group_grid.reshape(n, n)
            bound = _boundary(group_grid) | ~mask
            d = ndimage.distance_transform_edt(~bound)
            coast = fbm(gx, gy, 1.0/(size/9), 4, stable_hash(path+'coast') & 0xFFFF)
            # Straits vary: wide sounds in places, isthmuses where the noise dips.
            width = max(1.2, n*0.03)
            reach = width*(0.35+1.3*coast)
            # Small islands get narrow straits: nobody loses more than a third of their radius.
            core_depth = ndimage.maximum(d, group_grid+1, index=np.arange(group_grid.max()+2))
            sea = mask & (d < np.minimum(reach, 0.33*core_depth[group_grid+1]+0.6))
            if parent is None:
                edge = np.minimum(np.minimum(gx-x0, x0+size-gx), np.minimum(gy-y0, y0+size-gy))/cell
                sea |= edge < n*0.07*(1.0+0.5*coast)
            labels[sea] = SEA
            if parent is None:
                labels[~mask] = SEA   # open ocean all the way to the edge of the world
        border = ndimage.distance_transform_edt(~_boundary(labels)).astype(np.float32)

        # Per-child geometry.
        ys_idx, xs_idx = np.indices((n, n))
        flat_labels = labels.ravel()
        counts = np.bincount(flat_labels, minlength=FIRST_CHILD+k)
        sx = np.bincount(flat_labels, weights=xs_idx.ravel()+0.5, minlength=FIRST_CHILD+k)
        sy = np.bincount(flat_labels, weights=ys_idx.ravel()+0.5, minlength=FIRST_CHILD+k)
        home_cells = counts[HOME]
        if home_cells:
            hc = np.flatnonzero(flat_labels == HOME)
            mean = np.array([sx[HOME]/home_cells, sy[HOME]/home_cells])
            best = hc[np.argmin((hc % n+0.5-mean[0])**2+(hc // n+0.5-mean[1])**2)]
            capital = ((best % n+0.5)*cell+x0, (best // n+0.5)*cell+y0)   # a home cell, never the sea
        else:
            capital = (home_seed[0]*cell+x0, home_seed[1]*cell+y0)
        cap_u = np.array([(capital[0]-x0)/cell, (capital[1]-y0)/cell])
        near_home = ndimage.binary_dilation(labels == HOME, iterations=1)
        near_sea = ndimage.binary_dilation(labels == SEA, iterations=1)
        children = []
        for i, r in enumerate(subdirs):
            lab = FIRST_CHILD+i
            c = counts[lab]
            if c == 0:
                continue
            members = np.flatnonzero(flat_labels == lab)
            mx, my = members % n, members // n
            centroid = ((sx[lab]/c)*cell+x0, (sy[lab]/c)*cell+y0)
            # Drains to the capital when it shares land with it; a separate continent drains to the sea.
            same_land = member_group[i+1] == member_group[0]
            near_down = near_home if (same_land or not continental) else near_sea
            touching = members[near_down.ravel()[members]]
            pool = touching if len(touching) else members
            pu, pv = pool % n+0.5, pool // n+0.5
            pick = int(np.argmin((pu-cap_u[0])**2+(pv-cap_u[1])**2))
            child_outlet = (pu[pick]*cell+x0, pv[pick]*cell+y0)
            children.append({'id': r['id'], 'path': r['path'], 'name': self.display_name(r['path'], r['name']), 'label': lab,
                             'same_land': bool(same_land or not continental), 'scanned': bool(r['scanned']),
                             'error': r['error'], 'files': r['files'], 'dirs': r['dirs'], 'bytes': r['bytes'],
                             'newest': r['newest'], 'mtime': r['mtime'], 'day': r['day'], 'week': r['week'],
                             'kinds': json.loads(r['kinds']) if r['kinds'] else {}, 'unscanned': r['unscanned'],
                             'centroid': centroid, 'area': c*cell*cell, 'side': math.sqrt(c)*cell,
                             'bbox': (mx.min()*cell+x0, my.min()*cell+y0, (mx.max()+1)*cell+x0, (my.max()+1)*cell+y0),
                             'outlet': child_outlet, 'weight': float(weights[i+1])})
        t = Territory(node_id=node['id'], path=path, name=node['name'], depth=node['depth'], x0=x0, y0=y0, size=size, n=n,
                      labels=labels, border=border, children=children, capital=capital, outlet=outlet,
                      continental=continental, zone=zone, writable=writable, rivers=[], places={}, node=node, sig=sig,
                      version=self.index.version)
        t.child_by_label = {c['label']: c for c in children}
        if continental:
            # Distance to the coastline from either side: beaches on land, depth at sea.
            land = (labels != SEA) & (labels != OUT)
            t.sea_dist = (ndimage.distance_transform_edt(land) + ndimage.distance_transform_edt(~land)).astype(np.float32)
        if overflow:
            t.node = dict(node, overflow=len(overflow))
        t.node = dict(t.node, side=entry['side'] if entry else size*0.6)
        if parent is None:
            # The Linux disk is a continent without a single directory of its own.
            linux = member_group[owner] == 0
            if linux.any():
                lc = cells[linux]
                t.node['continent'] = {'name': 'Linux · WSL', 'centroid': ((lc[:, 0].mean())*cell+x0, (lc[:, 1].mean())*cell+y0),
                                       'side': math.sqrt(linux.sum())*cell}
        t.rivers = self._rivers(t)
        t.places = self._places(t, files, labels, cell)
        return t

    def _mounts(self, node, subdirs) -> int:
        return sum(1 for r in subdirs if os.path.ismount(r['path'])) if node['path'].count('/') <= 2 else 0

    def _continental(self, node, subdirs) -> bool:
        mounts = self._mounts(node, subdirs)
        big = sum(1 for r in subdirs if r['files']+r['dirs'] > 2000)
        return mounts >= 2 or (len(subdirs) >= 10 and big >= 4 and node['files'] >= 20000)

    def _archipelago_allowed(self, parent) -> bool:
        # One archipelago layer per branch: islands of islands read as cracked tiles.
        return parent is None or not parent.continental

    def _rivers(self, t: Territory) -> list:
        # Downstream is always toward the parent: tributaries run from each child to this
        # territory's capital, and the trunk carries everything out to its own outlet.
        rivers = []
        if t.outlet is not None:
            side = t.node['side']
            rivers.append({'pts': _meander(t.capital, t.outlet, stable_hash(t.path+'trunk')), 'width': 0.022*side, 'kind': 'trunk', 'child': None})
        for c in t.children:
            width = 0.022*c['side']
            upper = _meander(c['centroid'], c['outlet'], stable_hash(c['path']+'upper'))
            rivers.append({'pts': upper, 'width': width*0.8, 'kind': 'upper', 'child': c['label']})
            if c.get('same_land', True):
                rivers.append({'pts': _meander(c['outlet'], t.capital, stable_hash(c['path']+'lower'), 0.12), 'width': width, 'kind': 'lower', 'child': None})
        for r in rivers:
            p = r['pts']
            r['bbox'] = (p[:, 0].min()-r['width'], p[:, 1].min()-r['width'], p[:, 0].max()+r['width'], p[:, 1].max()+r['width'])
        return rivers

    def _places(self, t: Territory, files, labels, cell) -> dict:
        """Landmarks for the directory's own files, spread through its home district."""
        if not files:
            return {'n': 0}
        home = np.flatnonzero(labels.ravel() == HOME)
        if len(home) == 0:
            home = np.flatnonzero(labels.ravel() > SEA)
        n = t.n
        order = np.argsort([stable_hash(f"{t.path}#{i}") % 1000003 for i in range(len(home))], kind='stable')
        home = home[order]
        files = sorted(files, key=lambda r: r['name'].casefold())
        count = len(files)
        slots = home[(np.arange(count)*len(home))//count]
        jitter = np.array([[(stable_hash(r['path']) % 1000)/1000-0.5, ((stable_hash(r['path']) >> 12) % 1000)/1000-0.5] for r in files])
        per_cell = max(1.0, count/len(home))
        xs = (slots % n+0.5+jitter[:, 0]*0.8)*cell+t.x0
        ys = (slots // n+0.5+jitter[:, 1]*0.8)*cell+t.y0
        base = 0.42*math.sqrt(len(home)*cell*cell/count) if count else cell
        sizes = np.array([r['size'] or 0 for r in files], dtype=np.float64)
        radius = base*(0.55+0.45*np.clip(np.log10(sizes+1)/9, 0, 1))
        return {'n': count, 'ids': [r['id'] for r in files], 'names': [r['name'] for r in files], 'paths': [r['path'] for r in files],
                'kinds': [r['kind'] for r in files], 'x': xs, 'y': ys, 'r': radius, 'size': sizes,
                'mtime': np.array([r['mtime'] or 0 for r in files]), 'attrs': np.array([r['attrs'] or 0 for r in files]),
                'link': [bool(r['link']) for r in files], 'is_dir': [bool(r['is_dir']) for r in files], 'per_cell': per_cell}

    # ---------------------------------------------------------------- queries

    def descend(self, x: float, y: float, px: float, max_depth: int = 64):
        """Chain of (territory, child entry or None) from the root to the deepest place at
        (x, y) that is at least ~16 px across at pixel size px."""
        chain = []
        t = self.root()
        xs = np.array([x])
        ys = np.array([y])
        for _ in range(max_depth):
            lab, _ = label_at(t, xs, ys, px)
            lab = int(lab[0])
            entry = t.child_by_label.get(lab)
            chain.append((t, entry, lab))
            if entry is None or entry['side'] < px*16:
                break
            nxt = self.child(t, entry)
            if nxt is None:
                break
            t = nxt
        return chain
